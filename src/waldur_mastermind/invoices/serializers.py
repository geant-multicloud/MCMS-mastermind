import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from rest_framework import exceptions as rf_exceptions
from rest_framework import serializers
from rest_framework.reverse import reverse

from waldur_core.core import serializers as core_serializers
from waldur_core.core import signals as core_signals
from waldur_core.core import utils as core_utils
from waldur_core.structure import permissions as structure_permissions
from waldur_core.structure import serializers as structure_serializers
from waldur_mastermind.common.utils import quantize_price
from waldur_mastermind.marketplace import models as marketplace_models

from . import models, utils


class InvoiceItemSerializer(serializers.HyperlinkedModelSerializer):
    tax = serializers.DecimalField(max_digits=15, decimal_places=7)
    total = serializers.DecimalField(max_digits=15, decimal_places=7)
    factor = serializers.ReadOnlyField(source='get_factor')
    measured_unit = serializers.ReadOnlyField(source='get_measured_unit')
    resource_uuid = serializers.ReadOnlyField(source='resource.uuid')
    resource_name = serializers.ReadOnlyField(source='resource.name')
    project_uuid = serializers.ReadOnlyField(source='get_project_uuid')
    project_name = serializers.ReadOnlyField(source='get_project_name')
    details = serializers.JSONField()

    class Meta:
        model = models.InvoiceItem
        fields = (
            'uuid',
            'url',
            'name',
            'price',
            'tax',
            'total',
            'unit_price',
            'unit',
            'factor',
            'measured_unit',
            'start',
            'end',
            'article_code',
            'project_name',
            'project_uuid',
            'quantity',
            'details',
            'resource',
            'resource_uuid',
            'resource_name',
        )
        extra_kwargs = {
            'url': {'lookup_field': 'uuid', 'view_name': 'invoice-item-detail'},
            'resource': {
                'lookup_field': 'uuid',
                'view_name': 'marketplace-resource-detail',
            },
        }


class InvoiceItemDetailSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = models.InvoiceItem
        fields = (
            'invoice',
            'resource',
            'uuid',
            'article_code',
            'unit_price',
            'unit',
            'quantity',
            'measured_unit',
            'name',
            'start',
            'end',
            'details',
        )
        extra_kwargs = {
            'url': {'lookup_field': 'uuid', 'view_name': 'invoice-item-detail'},
            'invoice': {'lookup_field': 'uuid', 'view_name': 'invoice-detail'},
            'resource': {
                'lookup_field': 'uuid',
                'view_name': 'marketplace-resource-detail',
            },
        }

    def create(self, validated_data):
        resource = validated_data.get('resource')
        if resource:
            validated_data['project'] = resource.project
        return super().create(validated_data)


class InvoiceItemUpdateSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = models.InvoiceItem
        fields = ('article_code', 'quantity')
        extra_kwargs = {'quantity': {'required': False}}

    def update(self, instance, validated_data):
        """
        Update related ComponentUsage when quantity is being updated.
        """
        quantity = validated_data.get('quantity')
        if quantity is None:
            return super().update(instance, validated_data)

        invoice_item = instance
        resource = invoice_item.resource
        if not resource:
            raise ValidationError(
                _('Marketplace resource is not defined in invoice item.')
            )

        plan_component_id = invoice_item.details.get('plan_component_id')
        if not plan_component_id:
            raise ValidationError(
                _('Plan component is not defined in invoice item details.')
            )
        try:
            plan_component = marketplace_models.PlanComponent.objects.get(
                id=plan_component_id
            )
        except marketplace_models.PlanComponent.DoesNotExist:
            raise ValidationError(_('Related plan component is not found.'))
        offering_component = plan_component.component
        if (
            offering_component.billing_type
            != marketplace_models.OfferingComponent.BillingTypes.USAGE
        ):
            raise ValidationError(_('Offering component is not usage-based.'))

        component_usage = (
            marketplace_models.ComponentUsage.objects.filter(
                resource=resource,
                component=offering_component,
                billing_period__year=invoice_item.invoice.year,
                billing_period__month=invoice_item.invoice.month,
            )
            .order_by('date')
            .last()
        )
        if not component_usage:
            raise ValidationError(_('Component usage is not found.'))
        component_usage.usage = quantity
        component_usage.save(update_fields=['usage'])

        return super().update(instance, validated_data)


class InvoiceItemCompensationSerializer(serializers.Serializer):
    offering_component_name = serializers.CharField()


class InvoiceSerializer(
    core_serializers.RestrictedSerializerMixin, serializers.HyperlinkedModelSerializer
):
    price = serializers.DecimalField(max_digits=15, decimal_places=7)
    tax = serializers.DecimalField(max_digits=15, decimal_places=7)
    total = serializers.DecimalField(max_digits=15, decimal_places=7)
    items = serializers.SerializerMethodField()
    issuer_details = serializers.SerializerMethodField()
    customer_details = serializers.SerializerMethodField()
    due_date = serializers.DateField()
    file = serializers.SerializerMethodField()

    class Meta:
        model = models.Invoice
        fields = (
            'url',
            'uuid',
            'number',
            'customer',
            'price',
            'tax',
            'total',
            'state',
            'year',
            'month',
            'issuer_details',
            'invoice_date',
            'due_date',
            'customer',
            'customer_details',
            'items',
            'file',
            'backend_id',
            'payment_url',
            'reference_number',
        )
        extra_kwargs = {
            'url': {'lookup_field': 'uuid'},
            'customer': {'lookup_field': 'uuid'},
        }

    def get_issuer_details(self, invoice):
        return settings.WALDUR_INVOICES['ISSUER_DETAILS']

    def get_customer_details(self, invoice):
        return {
            'name': invoice.customer.name,
            'address': invoice.customer.address,
            'country': invoice.customer.country,
            'country_name': invoice.customer.get_country_display(),
            'email': invoice.customer.email,
            'postal': invoice.customer.postal,
            'phone_number': invoice.customer.phone_number,
            'bank_name': invoice.customer.bank_name,
            'bank_account': invoice.customer.bank_account,
        }

    def get_file(self, obj):
        return reverse(
            'invoice-pdf',
            kwargs={'uuid': obj.uuid.hex},
            request=self.context['request'],
        )

    def get_items(self, invoice):
        resource_uuid = self.context['request'].GET.get('resource_uuid')
        qs = invoice.items.all()

        if resource_uuid:
            if core_utils.is_uuid_like(resource_uuid):
                qs = qs.filter(resource__uuid=resource_uuid)
            else:
                raise rf_exceptions.ValidationError('Passed resource_uuid is not UUID.')

        qs = qs.order_by('project_name', 'name')
        items = utils.filter_invoice_items(qs)
        serializer = InvoiceItemSerializer(items, many=True, context=self.context)
        return serializer.data


class InvoiceItemReportSerializer(serializers.ModelSerializer):
    invoice_number = serializers.ReadOnlyField(source='invoice.number')
    invoice_uuid = serializers.ReadOnlyField(source='invoice.uuid')
    invoice_year = serializers.ReadOnlyField(source='invoice.year')
    invoice_month = serializers.ReadOnlyField(source='invoice.month')
    invoice_date = serializers.ReadOnlyField(source='invoice.invoice_date')
    due_date = serializers.ReadOnlyField(source='invoice.due_date')
    customer_uuid = serializers.ReadOnlyField(source='invoice.customer.uuid')
    customer_name = serializers.ReadOnlyField(source='invoice.customer.name')

    class Meta:
        model = models.InvoiceItem
        fields = (
            'customer_uuid',
            'customer_name',
            'project_uuid',
            'project_name',
            'invoice_uuid',
            'invoice_number',
            'invoice_year',
            'invoice_month',
            'invoice_date',
            'due_date',
            'invoice_price',
            'invoice_tax',
            'invoice_total',
            'name',
            'article_code',
            'price',
            'tax',
            'total',
            'unit_price',
            'unit',
            'start',
            'end',
        )
        decimal_fields = (
            'price',
            'tax',
            'total',
            'unit_price',
            'invoice_price',
            'invoice_tax',
            'invoice_total',
        )
        decimal_fields_extra_kwargs = {
            'invoice_price': {'source': 'invoice.price',},
            'invoice_tax': {'source': 'invoice.tax',},
            'invoice_total': {'source': 'invoice.total',},
        }

    def build_field(self, field_name, info, model_class, nested_depth):
        if field_name in self.Meta.decimal_fields:
            field_class = serializers.DecimalField
            field_kwargs = dict(max_digits=20, decimal_places=2, coerce_to_string=True,)
            default_kwargs = self.Meta.decimal_fields_extra_kwargs.get(field_name)
            if default_kwargs:
                field_kwargs.update(default_kwargs)
            return field_class, field_kwargs

        return super(InvoiceItemReportSerializer, self).build_field(
            field_name, info, model_class, nested_depth
        )

    def get_extra_kwargs(self):
        extra_kwargs = super(InvoiceItemReportSerializer, self).get_extra_kwargs()
        extra_kwargs.update(
            settings.WALDUR_INVOICES['INVOICE_REPORTING']['SERIALIZER_EXTRA_KWARGS']
        )
        return extra_kwargs


# SAF is accounting soft from Estonia: www.sysdec.ee/safsaf.htm
class SAFReportSerializer(serializers.Serializer):
    DOKNR = serializers.ReadOnlyField(source='invoice.number')
    KUUPAEV = serializers.SerializerMethodField(method_name='get_last_day_of_month')
    VORMKUUP = serializers.SerializerMethodField(method_name='get_invoice_date')
    MAKSEAEG = serializers.SerializerMethodField(method_name='get_due_date')
    YKSUS = serializers.ReadOnlyField(source='invoice.customer.agreement_number')
    PARTNER = serializers.SerializerMethodField(method_name='get_partner')
    ARTIKKEL = serializers.ReadOnlyField(source='article_code')
    KOGUS = serializers.IntegerField(source='quantity')
    SUMMA = serializers.SerializerMethodField(method_name='get_total')
    RMAKSUSUM = serializers.SerializerMethodField(method_name='get_tax')
    RMAKSULIPP = serializers.SerializerMethodField(method_name='get_vat')
    ARTPROJEKT = serializers.SerializerMethodField(method_name='get_project')
    ARTNIMI = serializers.SerializerMethodField(method_name='get_artnimi_field')
    VALI = serializers.SerializerMethodField(method_name='get_vali_field')
    U_KONEDEARV = serializers.SerializerMethodField(method_name='get_empty_field')
    U_GRUPPITUNNUS = serializers.ReadOnlyField(source='get_project_name')
    H_PERIOOD = serializers.SerializerMethodField(method_name='get_covered_period')

    class Meta:
        fields = (
            'DOKNR',
            'KUUPAEV',
            'VORMKUUP',
            'MAKSEAEG',
            'YKSUS',
            'PARTNER',
            'ARTIKKEL',
            'KOGUS',
            'SUMMA',
            'RMAKSUSUM',
            'RMAKSULIPP',
            'ARTPROJEKT',
            'ARTNIMI',
            'VALI',
            'U_KONEDEARV',
            'U_GRUPPITUNNUS',
            'H_PERIOOD',
        )

    def format_date(self, date):
        if date:
            return date.strftime('%d.%m.%Y')
        return ''

    def get_partner(self, invoice_item):
        customer = invoice_item.invoice.customer
        if customer.sponsor_number:
            return customer.sponsor_number
        else:
            return customer.agreement_number

    def get_first_day(self, invoice_item):
        year = invoice_item.invoice.year
        month = invoice_item.invoice.month
        return datetime.date(year=year, month=month, day=1)

    def get_last_day_of_month(self, invoice_item):
        first_day = self.get_first_day(invoice_item)
        last_day = core_utils.month_end(first_day)
        return self.format_date(last_day)

    def get_invoice_date(self, invoice_item):
        date = invoice_item.invoice.invoice_date
        return self.format_date(date)

    def get_due_date(self, invoice_item):
        date = invoice_item.invoice.due_date
        return self.format_date(date)

    def get_total(self, invoice_item):
        return quantize_price(invoice_item.price)

    def get_tax(self, invoice_item):
        # SAF expects a specific handling of rounding for VAT
        return invoice_item.tax.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def get_project(self, invoice_item):
        return settings.WALDUR_INVOICES['INVOICE_REPORTING']['SAF_PARAMS']['ARTPROJEKT']

    def get_vat(self, invoice_item):
        return settings.WALDUR_INVOICES['INVOICE_REPORTING']['SAF_PARAMS']['RMAKSULIPP']

    def get_vali_field(self, invoice_item):
        if invoice_item.invoice.customer.contact_details:
            return f'Record no {invoice_item.invoice.number}. {invoice_item.invoice.customer.contact_details}'
        else:
            return f'Record no {invoice_item.invoice.number}'

    def get_empty_field(self, invoice_item):
        return ''

    def get_artnimi_field(self, invoice_item):
        # If a single plan for an offering exists, skip it from display
        if invoice_item.resource and invoice_item.resource.offering.plans.count() == 1:
            return invoice_item.name
        if 'plan_name' in invoice_item.details.keys():
            return f'{invoice_item.name} / {invoice_item.details["plan_name"]}'
        else:
            return invoice_item.name

    def get_covered_period(self, invoice_item):
        first_day = self.get_first_day(invoice_item)
        last_day = core_utils.month_end(first_day)
        return '%s-%s' % (self.format_date(first_day), self.format_date(last_day))


class PaymentProfileSerializer(serializers.HyperlinkedModelSerializer):
    organization_uuid = serializers.ReadOnlyField(source='organization.uuid')
    payment_type_display = serializers.ReadOnlyField(source='get_payment_type_display')

    class Meta:
        model = models.PaymentProfile
        fields = (
            'uuid',
            'url',
            'name',
            'organization_uuid',
            'organization',
            'attributes',
            'payment_type',
            'payment_type_display',
            'is_active',
        )
        extra_kwargs = {
            'url': {'view_name': 'payment-profile-detail', 'lookup_field': 'uuid',},
            'organization': {'view_name': 'customer-detail', 'lookup_field': 'uuid',},
        }


class PaymentSerializer(
    structure_serializers.ProtectedMediaSerializerMixin,
    serializers.HyperlinkedModelSerializer,
):
    profile = serializers.HyperlinkedRelatedField(
        view_name='payment-profile-detail',
        lookup_field='uuid',
        queryset=models.PaymentProfile.objects.filter(is_active=True),
    )
    invoice = serializers.HyperlinkedRelatedField(
        view_name='invoice-detail', lookup_field='uuid', read_only=True,
    )
    invoice_uuid = serializers.ReadOnlyField(source='invoice.uuid')
    invoice_period = serializers.SerializerMethodField(method_name='get_invoice_period')
    customer_uuid = serializers.ReadOnlyField(source='profile.organization.uuid')

    def get_invoice_period(self, payment):
        if payment.invoice:
            return '%02d-%s' % (payment.invoice.month, payment.invoice.year)

    class Meta:
        model = models.Payment
        fields = (
            'uuid',
            'url',
            'profile',
            'date_of_payment',
            'sum',
            'proof',
            'invoice',
            'invoice_uuid',
            'invoice_period',
            'customer_uuid',
        )
        extra_kwargs = {
            'url': {'view_name': 'payment-detail', 'lookup_field': 'uuid'},
        }


class PaidSerializer(serializers.Serializer):
    date = serializers.DateField(required=True)
    proof = serializers.FileField(required=False)


class LinkToInvoiceSerializer(serializers.Serializer):
    invoice = serializers.HyperlinkedRelatedField(
        view_name='invoice-detail',
        lookup_field='uuid',
        queryset=models.Invoice.objects.filter(state=models.Invoice.States.PAID),
    )


class BackendIdSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Invoice
        fields = ('backend_id',)


class PaymentURLSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Invoice
        fields = ('payment_url',)


class ReferenceNumberSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Invoice
        fields = ('reference_number',)


def get_payment_profiles(serializer, customer):
    user = serializer.context['request'].user
    if user.is_staff or user.is_support:
        return PaymentProfileSerializer(
            customer.paymentprofile_set.all(),
            many=True,
            context={'request': serializer.context['request']},
        ).data

    if structure_permissions._has_owner_access(user, customer):
        return PaymentProfileSerializer(
            customer.paymentprofile_set.filter(is_active=True),
            many=True,
            context={'request': serializer.context['request']},
        ).data


def add_payment_profile(sender, fields, **kwargs):
    fields['payment_profiles'] = serializers.SerializerMethodField()
    setattr(sender, 'get_payment_profiles', get_payment_profiles)


core_signals.pre_serializer_fields.connect(
    sender=structure_serializers.CustomerSerializer, receiver=add_payment_profile,
)
