from datetime import date
from unittest import mock

from rest_framework import status, test

from waldur_mastermind.invoices.tests import factories, fixtures
from waldur_mastermind.marketplace import models as marketplace_models
from waldur_mastermind.marketplace.tests import factories as marketplace_factories


class InvoiceItemDeleteTest(test.APITransactionTestCase):
    def setUp(self):
        self.fixture = fixtures.InvoiceFixture()

    def delete_invoice_item(self, user):
        self.client.force_authenticate(user)
        return self.client.delete(
            factories.InvoiceItemFactory.get_url(self.fixture.invoice_item),
        )

    def test_staff_can_delete_invoice_item(self):
        response = self.delete_invoice_item(self.fixture.staff)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_non_staff_can_not_delete_invoice_item(self):
        response = self.delete_invoice_item(self.fixture.user)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @mock.patch('waldur_core.structure.handlers.event_logger')
    def test_event_is_emitted(self, logger_mock):
        self.delete_invoice_item(self.fixture.staff)
        logger_mock.event_logger.invoice_item.info(
            f'Invoice item {self.fixture.invoice_item.name} has been deleted.',
            event_type='invoice_item_deleted',
            event_context={'customer': self.fixture.invoice_item.invoice.customer,},
        )


class InvoiceItemUpdateTest(test.APITransactionTestCase):
    def setUp(self):
        self.fixture = fixtures.InvoiceFixture()

    def update_invoice_item(self, user):
        self.client.force_authenticate(user)
        return self.client.patch(
            factories.InvoiceItemFactory.get_url(self.fixture.invoice_item),
            {'article_code': 'AA11'},
        )

    def test_staff_can_update_invoice_item(self):
        response = self.update_invoice_item(self.fixture.staff)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.fixture.invoice_item.refresh_from_db()
        self.assertEqual('AA11', self.fixture.invoice_item.article_code)

    def test_non_staff_can_not_update_invoice_item(self):
        response = self.update_invoice_item(self.fixture.user)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @mock.patch('waldur_core.structure.handlers.event_logger')
    def test_event_is_emitted(self, logger_mock):
        self.update_invoice_item(self.fixture.staff)
        logger_mock.event_logger.invoice_item.info(
            f'Invoice item {self.fixture.invoice_item.name} has been updated.',
            event_type='invoice_item_updated',
            event_context={'customer': self.fixture.invoice_item.invoice.customer,},
        )

    def test_when_quantity_is_updated_component_usage_is_updated_too(self):
        # Arrange
        item = self.fixture.invoice_item
        resource = marketplace_factories.ResourceFactory()
        offering = resource.offering
        item.resource = resource
        offering_component = marketplace_factories.OfferingComponentFactory(
            offering=offering,
            billing_type=marketplace_models.OfferingComponent.BillingTypes.USAGE,
        )
        plan = marketplace_factories.PlanFactory(offering=offering,)
        plan_component = marketplace_factories.PlanComponentFactory(
            plan=plan, component=offering_component
        )
        item.details['plan_component_id'] = plan_component.id
        item.save()
        billing_period = date(year=item.invoice.year, month=item.invoice.month, day=1)
        component_usage = marketplace_factories.ComponentUsageFactory(
            resource=resource,
            component=offering_component,
            billing_period=billing_period,
            usage=100,
        )

        # Act
        self.client.force_authenticate(self.fixture.staff)
        response = self.client.patch(
            factories.InvoiceItemFactory.get_url(self.fixture.invoice_item),
            {'quantity': 200},
        )

        # Assert
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        component_usage.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(component_usage.usage, 200)
        self.assertEqual(item.quantity, 200)


class InvoiceItemCompensationTest(test.APITransactionTestCase):
    def setUp(self):
        self.fixture = fixtures.InvoiceFixture()
        self.item = self.fixture.invoice_item

    def create_compensation(self, user, offering_component_name='Compensation'):
        self.client.force_authenticate(user)
        url = factories.InvoiceItemFactory.get_url(self.item, 'create_compensation')
        return self.client.post(
            url, {'offering_component_name': offering_component_name}
        )

    def test_staff_can_create_compensation(self):
        response = self.create_compensation(self.fixture.staff)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_new_invoice_item_has_valid_details(self):
        self.create_compensation(self.fixture.staff)
        new_invoice_item = self.fixture.invoice.items.last()
        self.assertEqual(
            str(new_invoice_item.details['original_invoice_item_uuid']),
            str(self.item.uuid),
        )
        self.assertEqual(
            new_invoice_item.details['offering_component_name'], 'Compensation'
        )

    def test_compensation_for_invoice_item_with_negative_price_is_invalid(self):
        self.item.unit_price *= -1
        self.item.save()
        response = self.create_compensation(self.fixture.staff)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_staff_can_not_create_compensation(self):
        response = self.create_compensation(self.fixture.user)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @mock.patch('waldur_core.structure.handlers.event_logger')
    def test_event_is_emitted(self, logger_mock):
        self.create_compensation(self.fixture.staff)
        logger_mock.event_logger.invoice_item.info(
            f'Invoice item {self.item.name} has been created.',
            event_type='invoice_item_created',
            event_context={'customer': self.item.invoice.customer,},
        )
