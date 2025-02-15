import collections
import datetime
import logging

from celery import shared_task
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import status

from waldur_core.core import utils as core_utils
from waldur_core.structure import models as structure_models
from waldur_mastermind.common.utils import create_request
from waldur_mastermind.invoices import models as invoices_models
from waldur_mastermind.invoices import utils as invoice_utils

from . import exceptions, models, utils, views

logger = logging.getLogger(__name__)


User = get_user_model()


def approve_order(order, user):
    order.approve()
    order.approved_by = user
    order.approved_at = timezone.now()
    order.save()

    serialized_order = core_utils.serialize_instance(order)
    serialized_user = core_utils.serialize_instance(user)
    transaction.on_commit(
        lambda: process_order.delay(serialized_order, serialized_user)
    )


@shared_task
def process_order(serialized_order, serialized_user):
    # Skip remote plugin because it is going to processed
    # only after it gets approved by service provider
    from waldur_mastermind.marketplace_remote import PLUGIN_NAME as REMOTE_PLUGIN_NAME

    order = core_utils.deserialize_instance(serialized_order)
    user = core_utils.deserialize_instance(serialized_user)
    for item in order.items.exclude(offering__type=REMOTE_PLUGIN_NAME):
        item.set_state_executing()
        item.save(update_fields=['state'])
        utils.process_order_item(item, user)


@shared_task
def process_order_item(serialized_order_item, serialized_user):
    order_item = core_utils.deserialize_instance(serialized_order_item)
    user = core_utils.deserialize_instance(serialized_user)
    utils.process_order_item(order_item, user)


@shared_task
def create_screenshot_thumbnail(uuid):
    screenshot = models.Screenshot.objects.get(uuid=uuid)
    utils.create_screenshot_thumbnail(screenshot)


@shared_task
def notify_order_approvers(uuid):
    order = models.Order.objects.get(uuid=uuid)
    users = order.get_approvers()
    emails = [u.email for u in users if u.email]
    link = core_utils.format_homeport_link(
        'projects/{project_uuid}/marketplace-order-list/',
        project_uuid=order.project.uuid,
    )

    context = {
        'order_url': link,
        'order': order,
        'site_name': settings.WALDUR_CORE['SITE_NAME'],
    }

    core_utils.broadcast_mail('marketplace', 'notification_approval', context, emails)


@shared_task
def notify_about_resource_change(event_type, context, resource_uuid):
    resource = models.Resource.objects.get(uuid=resource_uuid)
    project = structure_models.Project.all_objects.get(id=resource.project_id)
    emails = project.get_users().values_list('email', flat=True)
    core_utils.broadcast_mail('marketplace', event_type, context, emails)


def filter_aggregate_by_scope(queryset, scope):
    scope_path = None

    if isinstance(scope, structure_models.Project):
        scope_path = 'resource__project'

    if isinstance(scope, structure_models.Customer):
        scope_path = 'resource__project__customer'

    if scope_path:
        queryset = queryset.filter(**{scope_path: scope})

    return queryset


def aggregate_reported_usage(start, end, scope):
    queryset = models.ComponentUsage.objects.filter(
        date__gte=start, date__lte=end
    ).exclude(component__parent=None)

    queryset = filter_aggregate_by_scope(queryset, scope)

    queryset = queryset.values('component__parent_id').annotate(total=Sum('usage'))

    return {row['component__parent_id']: row['total'] for row in queryset}


def aggregate_fixed_usage(start, end, scope):
    queryset = models.ResourcePlanPeriod.objects.filter(
        # Resource has been active during billing period
        Q(start__gte=start, end__lte=end)
        | Q(end__isnull=True)  # Resource is still active
        | Q(
            end__gte=start, end__lte=end
        )  # Resource has been launched in previous billing period and stopped in current
    )
    queryset = filter_aggregate_by_scope(queryset, scope)

    queryset = queryset.values('plan__components__component__parent_id').annotate(
        total=Sum('plan__components__amount')
    )

    return {
        row['plan__components__component__parent_id']: row['total'] for row in queryset
    }


def calculate_usage_for_scope(start, end, scope):
    reported_usage = aggregate_reported_usage(start, end, scope)
    fixed_usage = aggregate_fixed_usage(start, end, scope)
    # It needs to cover a case when a key is None because OfferingComponent.parent can be None.
    fixed_usage.pop(None, None)
    components = set(reported_usage.keys()) | set(fixed_usage.keys())
    content_type = ContentType.objects.get_for_model(scope)

    for component_id in components:
        models.CategoryComponentUsage.objects.update_or_create(
            content_type=content_type,
            object_id=scope.id,
            component_id=component_id,
            date=start,
            defaults={
                'reported_usage': reported_usage.get(component_id),
                'fixed_usage': fixed_usage.get(component_id),
            },
        )


@shared_task(name='waldur_mastermind.marketplace.calculate_usage_for_current_month')
def calculate_usage_for_current_month():
    start = invoice_utils.get_current_month_start()
    end = invoice_utils.get_current_month_end()
    scopes = []

    for customer in structure_models.Customer.objects.all():
        scopes.append(customer)
        for project in customer.projects.all():
            scopes.append(project)

    for scope in scopes:
        calculate_usage_for_scope(start, end, scope)


@shared_task(name='waldur_mastermind.marketplace.send_notifications_about_usages')
def send_notifications_about_usages():
    for warning in utils.get_info_about_missing_usage_reports():
        customer = warning['customer']
        emails = [owner.email for owner in customer.get_owners()]
        warning['public_resources_url'] = utils.get_public_resources_url(customer)

        if customer.serviceprovider.enable_notifications and emails:
            core_utils.broadcast_mail(
                'marketplace', 'notification_usages', warning, emails
            )


@shared_task
def terminate_resource(serialized_resource, serialized_user):
    resource = core_utils.deserialize_instance(serialized_resource)
    user = core_utils.deserialize_instance(serialized_user)
    view = views.ResourceViewSet.as_view({'post': 'terminate'})
    response = create_request(view, user, {}, uuid=resource.uuid.hex)

    if response.status_code != status.HTTP_200_OK:
        raise exceptions.ResourceTerminateException(response.rendered_content)


@shared_task(
    name='waldur_mastermind.marketplace.terminate_resources_if_project_end_date_has_been_reached'
)
def terminate_resources_if_project_end_date_has_been_reached():
    expired_projects = structure_models.Project.objects.exclude(
        end_date__isnull=True
    ).filter(end_date__lte=timezone.datetime.today())

    for project in expired_projects:
        resources = models.Resource.objects.filter(project=project).filter(
            state__in=(models.Resource.States.OK, models.Resource.States.ERRED)
        )

        if resources:
            utils.schedule_resources_termination(resources)
        else:
            project.delete()


@shared_task(name='waldur_mastermind.marketplace.notify_about_stale_resource')
def notify_about_stale_resource():
    if not settings.WALDUR_MARKETPLACE['ENABLE_STALE_RESOURCE_NOTIFICATIONS']:
        return

    today = datetime.datetime.today()
    prev_1 = today - relativedelta(months=1)
    prev_2 = today - relativedelta(months=2)
    items = invoices_models.InvoiceItem.objects.filter(
        Q(invoice__month=today.month, invoice__year=today.year,)
        | Q(invoice__month=prev_1.month, invoice__year=prev_1.year)
        | Q(invoice__month=prev_2.month, invoice__year=prev_2.year)
    )
    actual_resources_ids = []

    for item in items:
        if item.price:
            actual_resources_ids.append(item.resource.id)

    resources = (
        models.Resource.objects.exclude(id__in=actual_resources_ids)
        .exclude(
            Q(state=models.Resource.States.TERMINATED)
            | Q(state=models.Resource.States.TERMINATING)
            | Q(state=models.Resource.States.CREATING)
        )
        .exclude(offering__billable=False)
    )
    user_resources = collections.defaultdict(list)

    for resource in resources:
        owners = resource.project.customer.get_owners().exclude(email='')
        resource_url = core_utils.format_homeport_link(
            '/projects/{project_uuid}/marketplace-project-resource-details/{resource_uuid}/',
            project_uuid=resource.project.uuid.hex,
            resource_uuid=resource.uuid.hex,
        )

        for user in owners:
            user_resources[user.email].append(
                {'resource': resource, 'resource_url': resource_url}
            )

    for key, value in user_resources.items():
        core_utils.broadcast_mail(
            'marketplace',
            'notification_about_stale_resources',
            {'resources': value},
            [key],
        )


@shared_task(
    name='waldur_mastermind.marketplace.terminate_resource_if_its_end_date_has_been_reached'
)
def terminate_resource_if_its_end_date_has_been_reached():
    expired_resources = models.Resource.objects.exclude(
        end_date__isnull=True,
        state__in=(
            models.Resource.States.TERMINATED,
            models.Resource.States.TERMINATING,
        ),
    ).filter(end_date__lte=timezone.datetime.today())

    utils.schedule_resources_termination(expired_resources)


@shared_task
def notify_about_resource_termination(resource_uuid, user_uuid, is_staff_action=None):
    resource = models.Resource.objects.get(uuid=resource_uuid)
    user = User.objects.get(uuid=user_uuid)
    admin_emails = set(
        resource.project.get_users(structure_models.ProjectRole.ADMINISTRATOR)
        .exclude(email='')
        .values_list('email', flat=True)
    )
    manager_emails = set(
        resource.project.get_users(structure_models.ProjectRole.MANAGER)
        .exclude(email='')
        .values_list('email', flat=True)
    )
    emails = admin_emails | manager_emails
    resource_url = core_utils.format_homeport_link(
        '/projects/{project_uuid}/marketplace-project-resource-details/{resource_uuid}/',
        project_uuid=resource.project.uuid.hex,
        resource_uuid=resource.uuid.hex,
    )
    context = {'resource': resource, 'user': user, 'resource_url': resource_url}

    if is_staff_action:
        core_utils.broadcast_mail(
            'marketplace',
            'marketplace_resource_terminatate_scheduled_staff',
            context,
            emails,
        )
    else:
        core_utils.broadcast_mail(
            'marketplace', 'marketplace_resource_terminatate_scheduled', context, emails
        )
