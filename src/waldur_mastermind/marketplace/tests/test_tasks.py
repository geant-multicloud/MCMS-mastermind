import datetime
from unittest.mock import patch

from django.core import mail
from django.utils import timezone
from freezegun import freeze_time
from rest_framework import test

from waldur_core.core import utils as core_utils
from waldur_core.structure.tests import factories as structure_factories
from waldur_core.structure.tests import fixtures as structure_fixtures
from waldur_mastermind.invoices import models as invoices_models
from waldur_mastermind.invoices.tests import factories as invoices_factories
from waldur_mastermind.marketplace import exceptions, models, tasks
from waldur_mastermind.marketplace.tests.helpers import override_marketplace_settings

from . import factories, fixtures


class CalculateUsageForCurrentMonthTest(test.APITransactionTestCase):
    def setUp(self):
        offering = factories.OfferingFactory()
        plan = factories.PlanFactory(offering=offering)
        resource = factories.ResourceFactory(offering=offering)
        category_component = factories.CategoryComponentFactory()
        self.offering_component = factories.OfferingComponentFactory(
            offering=offering,
            parent=category_component,
            billing_type=models.OfferingComponent.BillingTypes.USAGE,
        )
        factories.PlanComponentFactory(plan=plan, component=self.offering_component)
        plan_period = models.ResourcePlanPeriod.objects.create(
            resource=resource, plan=plan, start=timezone.now()
        )
        models.ComponentUsage.objects.create(
            resource=resource,
            component=self.offering_component,
            usage=10,
            date=datetime.datetime.now(),
            billing_period=core_utils.month_start(datetime.datetime.now()),
            plan_period=plan_period,
        )

    def test_calculate_usage_if_category_component_is_set(self):
        tasks.calculate_usage_for_current_month()
        self.assertEqual(models.CategoryComponentUsage.objects.count(), 2)

    def test_calculate_usage_if_category_component_is_not_set(self):
        self.offering_component.parent = None
        self.offering_component.save()
        tasks.calculate_usage_for_current_month()
        self.assertEqual(models.CategoryComponentUsage.objects.count(), 0)


class NotificationTest(test.APITransactionTestCase):
    def test_notify_about_resource_change(self):
        project_fixture = structure_fixtures.ProjectFixture()
        admin = project_fixture.admin
        project = project_fixture.project
        resource = factories.ResourceFactory(project=project, name='Test resource')
        tasks.notify_about_resource_change(
            'marketplace_resource_create_succeeded',
            {'resource_name': resource.name},
            resource.uuid,
        )
        self.assertEqual(len(mail.outbox), 1)
        subject_template_name = '%s/%s_subject.txt' % (
            'marketplace',
            'marketplace_resource_create_succeeded',
        )
        subject = core_utils.format_text(
            subject_template_name, {'resource_name': resource.name}
        )
        self.assertEqual(mail.outbox[0].subject, subject)
        self.assertEqual(mail.outbox[0].to[0], admin.email)
        self.assertTrue(resource.name in mail.outbox[0].body)
        self.assertTrue(resource.name in mail.outbox[0].subject)


class ResourceEndDateTest(test.APITransactionTestCase):
    def test_notify_about_resource_scheduled_termination(self):
        fixture = fixtures.MarketplaceFixture()
        admin = fixture.admin
        manager = fixture.manager
        tasks.notify_about_resource_termination(
            fixture.resource.uuid, fixture.offering_owner.uuid,
        )
        recipients = {m.to[0] for m in mail.outbox}
        self.assertEqual(recipients, {admin.email, manager.email})
        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(fixture.resource.name in mail.outbox[0].body)
        self.assertTrue(fixture.resource.name in mail.outbox[0].subject)

    def test_mail_is_not_sent_if_there_are_no_project_admin_or_manager(self):
        fixture = fixtures.MarketplaceFixture()
        tasks.notify_about_resource_termination(
            fixture.resource.uuid, fixture.offering_owner.uuid,
        )
        self.assertEqual(len(mail.outbox), 0)

    @patch('waldur_mastermind.marketplace.tasks.core_utils.broadcast_mail')
    def test_notification_uses_different_templates_for_staff_and_other_users(
        self, mock_broadcast_mail
    ):
        fixture = fixtures.MarketplaceFixture()
        tasks.notify_about_resource_termination(
            fixture.resource.uuid, fixture.offering_owner.uuid, False
        )
        mock_broadcast_mail.assert_called()
        self.assertEqual(
            mock_broadcast_mail.call_args[0][1],
            'marketplace_resource_terminatate_scheduled',
        )

        tasks.notify_about_resource_termination(
            fixture.resource.uuid, fixture.offering_owner.uuid, True
        )
        mock_broadcast_mail.assert_called()
        self.assertEqual(
            mock_broadcast_mail.call_args[0][1],
            'marketplace_resource_terminatate_scheduled_staff',
        )


class TerminateResource(test.APITransactionTestCase):
    def setUp(self):
        fixture = structure_fixtures.UserFixture()
        self.user = fixture.staff
        offering = factories.OfferingFactory()
        self.resource = factories.ResourceFactory(offering=offering)
        factories.OrderItemFactory(
            resource=self.resource,
            type=models.OrderItem.Types.TERMINATE,
            state=models.OrderItem.States.EXECUTING,
        )

    def test_raise_exception_if_order_item_has_not_been_created(self):
        self.assertRaises(
            exceptions.ResourceTerminateException,
            tasks.terminate_resource,
            core_utils.serialize_instance(self.resource),
            core_utils.serialize_instance(self.user),
        )


class ProjectEndDate(test.APITransactionTestCase):
    def setUp(self):
        # We need create a system robot account because
        # account created in a migration does not exist when test is running
        structure_factories.UserFactory(
            first_name='System',
            last_name='Robot',
            username='system_robot',
            description='Special user used for performing actions on behalf of Waldur.',
            is_staff=True,
            is_active=True,
        )
        core_utils.get_system_robot.cache_clear()
        self.fixtures = fixtures.MarketplaceFixture()
        self.fixtures.project.end_date = datetime.datetime(
            day=1, month=1, year=2020
        ).date()
        self.fixtures.project.save()
        self.fixtures.resource.set_state_ok()
        self.fixtures.resource.save()

    def test_terminate_resources_if_project_end_date_has_been_reached(self):
        with freeze_time('2020-01-02'):
            tasks.terminate_resources_if_project_end_date_has_been_reached()
            self.assertTrue(
                models.OrderItem.objects.filter(
                    resource=self.fixtures.resource,
                    type=models.OrderItem.Types.TERMINATE,
                ).count()
            )
            order_item = models.OrderItem.objects.get(
                resource=self.fixtures.resource, type=models.OrderItem.Types.TERMINATE
            )
            self.assertTrue(order_item.order.state, models.Order.States.EXECUTING)


@override_marketplace_settings(ENABLE_STALE_RESOURCE_NOTIFICATIONS=True)
class NotificationAboutStaleResourceTest(test.APITransactionTestCase):
    def setUp(self):
        project_fixture = structure_fixtures.ProjectFixture()
        self.owner = project_fixture.owner
        project = project_fixture.project
        self.resource = factories.ResourceFactory(
            project=project, name='Test resource', state=models.Resource.States.OK
        )
        self.resource.offering.type = 'Test.Type'
        self.resource.offering.save()

    def test_send_notify_if_stale_resource_exists(self):
        tasks.notify_about_stale_resource()
        self.assertEqual(len(mail.outbox), 1)
        subject_template_name = '%s/%s_subject.txt' % (
            'marketplace',
            'notification_about_stale_resources',
        )
        subject = core_utils.format_text(subject_template_name, {})
        self.assertEqual(mail.outbox[0].subject, subject)
        self.assertEqual(mail.outbox[0].to[0], self.owner.email)
        self.assertTrue(self.resource.name in mail.outbox[0].body)

    def test_do_not_send_notify_if_stale_resource_does_not_exists(self):
        item = invoices_factories.InvoiceItemFactory(resource=self.resource)
        item.unit_price = 10
        item.quantity = 10
        item.unit = invoices_models.InvoiceItem.Units.QUANTITY
        item.save()

        self.assertTrue(item.price)
        tasks.notify_about_stale_resource()
        self.assertEqual(len(mail.outbox), 0)

    def test_send_notify_if_related_invoice_item_has_not_price(self):
        item = invoices_factories.InvoiceItemFactory(resource=self.resource)
        item.unit_price = 0
        item.save()
        self.assertFalse(item.price)
        tasks.notify_about_stale_resource()
        self.assertEqual(len(mail.outbox), 1)

    def test_send_notify_only_for_resources_belonging_to_billable_offerings(self):
        self.resource.offering.billable = False
        self.resource.offering.save()
        tasks.notify_about_stale_resource()
        self.assertEqual(len(mail.outbox), 0)

    @override_marketplace_settings(ENABLE_STALE_RESOURCE_NOTIFICATIONS=False)
    def test_do_not_send_notify_if_configuration_is_false(self):
        tasks.notify_about_stale_resource()
        self.assertEqual(len(mail.outbox), 0)


class ResourceEndDate(test.APITransactionTestCase):
    def setUp(self):
        # We need create a system robot account because
        # account created in a migration does not exist when test is running
        structure_factories.UserFactory(
            first_name='System',
            last_name='Robot',
            username='system_robot',
            description='Special user used for performing actions on behalf of Waldur.',
            is_staff=True,
            is_active=True,
        )
        core_utils.get_system_robot.cache_clear()
        self.fixtures = fixtures.MarketplaceFixture()
        self.resource = self.fixtures.resource
        self.resource.end_date = datetime.datetime(day=1, month=1, year=2020).date()
        self.resource.set_state_ok()
        self.resource.save()

    def test_terminate_resource_if_its_end_date_has_been_reached(self):
        with freeze_time('2020-01-01'):
            self.assertTrue(self.resource.is_expired)
            tasks.terminate_resource_if_its_end_date_has_been_reached()
            self.resource.refresh_from_db()

            self.assertTrue(
                models.OrderItem.objects.filter(
                    resource=self.fixtures.resource,
                    type=models.OrderItem.Types.TERMINATE,
                ).count()
            )
            order_item = models.OrderItem.objects.get(
                resource=self.fixtures.resource, type=models.OrderItem.Types.TERMINATE
            )
            self.assertTrue(order_item.order.state, models.Order.States.EXECUTING)
