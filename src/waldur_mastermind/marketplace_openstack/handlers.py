import logging

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from waldur_core.core import utils as core_utils
from waldur_core.structure import models as structure_models
from waldur_mastermind.marketplace import models as marketplace_models
from waldur_mastermind.marketplace.utils import get_resource_state
from waldur_openstack.openstack import models as openstack_models
from waldur_openstack.openstack_tenant import apps as openstack_tenant_apps
from waldur_openstack.openstack_tenant import models as openstack_tenant_models

from . import INSTANCE_TYPE, STORAGE_MODE_FIXED, TENANT_TYPE, VOLUME_TYPE, tasks, utils

logger = logging.getLogger(__name__)
States = marketplace_models.Resource.States


def create_offering_from_tenant(sender, instance, created=False, **kwargs):
    if created:
        return

    if not instance.tracker.has_changed('state'):
        return

    if instance.tracker.previous('state') != instance.States.CREATING:
        return

    if instance.state != instance.States.OK:
        return

    create_offerings_for_volume_and_instance(instance)


def create_offerings_for_volume_and_instance(tenant):
    if not settings.WALDUR_MARKETPLACE_OPENSTACK[
        'AUTOMATICALLY_CREATE_PRIVATE_OFFERING'
    ]:
        return

    try:
        resource = marketplace_models.Resource.objects.get(scope=tenant)
    except ObjectDoesNotExist:
        logger.debug(
            'Skipping offering creation for tenant because order '
            'item does not exist. OpenStack tenant ID: %s',
            tenant.id,
        )
        return

    try:
        service_settings = structure_models.ServiceSettings.objects.get(
            scope=tenant, type=openstack_tenant_apps.OpenStackTenantConfig.service_name,
        )
    except ObjectDoesNotExist:
        logger.debug(
            'Skipping offering creation for tenant because service settings '
            'object does not exist. OpenStack tenant ID: %s',
            tenant.id,
        )
        return

    parent_offering = resource.offering
    for offering_type in (INSTANCE_TYPE, VOLUME_TYPE):
        try:
            category, offering_name = utils.get_category_and_name_for_offering_type(
                offering_type, service_settings
            )
        except ObjectDoesNotExist:
            logger.warning(
                'Skipping offering creation for tenant because category '
                'for instances and volumes is not yet defined.'
            )
            continue
        actual_customer = tenant.project.customer
        payload = dict(
            type=offering_type,
            name=offering_name,
            scope=service_settings,
            shared=False,
            category=category,
            # OpenStack instance and volume offerings are charged as a part of its tenant
            billable=False,
            parent=parent_offering,
            customer=actual_customer,
            project=tenant.project,
        )

        fields = (
            'state',
            'attributes',
            'thumbnail',
            'vendor_details',
            'latitude',
            'longitude',
        )
        for field in fields:
            payload[field] = getattr(parent_offering, field)

        marketplace_models.Offering.objects.create(**payload)


def archive_offering(sender, instance, **kwargs):
    service_settings = instance

    if (
        service_settings.type
        == openstack_tenant_apps.OpenStackTenantConfig.service_name
        and service_settings.content_type
        == ContentType.objects.get_for_model(openstack_models.Tenant)
    ):
        marketplace_models.Offering.objects.filter(scope=service_settings).update(
            state=marketplace_models.Offering.States.ARCHIVED
        )


def synchronize_volume_metadata(sender, instance, created=False, **kwargs):
    volume = instance
    if not created and not set(volume.tracker.changed()) & {
        'size',
        'instance_id',
        'type_id',
    }:
        return

    try:
        resource = marketplace_models.Resource.objects.get(scope=volume)
    except ObjectDoesNotExist:
        logger.debug(
            'Skipping resource synchronization for OpenStack volume '
            'because marketplace resource does not exist. '
            'Resource ID: %s',
            instance.id,
        )
        return

    utils.import_volume_metadata(resource)


def synchronize_instance_name(sender, instance, created=False, **kwargs):
    if not created and not set(instance.tracker.changed()) & {'name'}:
        return

    for volume in instance.volumes.all():
        try:
            resource = marketplace_models.Resource.objects.get(scope=volume)
        except ObjectDoesNotExist:
            logger.debug(
                'Skipping resource synchronization for OpenStack volume '
                'because marketplace resource does not exist. '
                'Resource ID: %s',
                instance.id,
            )
            continue

        resource.backend_metadata['instance_name'] = volume.instance.name
        resource.save(update_fields=['backend_metadata'])


def synchronize_instance_after_pull(sender, instance, **kwargs):
    if (
        instance.tracker.has_changed('action')
        and instance.tracker.previous('action') == 'Pull'
    ):
        try:
            resource = marketplace_models.Resource.objects.get(scope=instance)
            utils.import_instance_metadata(resource)
        except ObjectDoesNotExist:
            pass


def synchronize_internal_ips(sender, instance, created=False, **kwargs):
    internal_ip = instance
    if not created and not set(internal_ip.tracker.changed()) & {
        'fixed_ips',
        'instance_id',
    }:
        return

    vms = {
        vm
        for vm in (internal_ip.instance_id, internal_ip.tracker.previous('instance_id'))
        if vm
    }

    for vm in vms:
        try:
            scope = openstack_tenant_models.Instance.objects.get(id=vm)
            resource = marketplace_models.Resource.objects.get(scope=scope)
        except ObjectDoesNotExist:
            logger.debug(
                'Skipping resource synchronization for OpenStack instance '
                'because marketplace resource does not exist. '
                'Resource ID: %s',
                vm,
            )
            continue

        utils.import_instance_metadata(resource)


def synchronize_floating_ips(sender, instance, created=False, **kwargs):
    floating_ip = instance
    if not created and not set(instance.tracker.changed()) & {
        'address',
        'internal_ip_id',
    }:
        return

    internal_ips = {
        ip
        for ip in (
            floating_ip.internal_ip_id,
            floating_ip.tracker.previous('internal_ip_id'),
        )
        if ip
    }
    for ip_id in internal_ips:
        try:
            scope = openstack_tenant_models.Instance.objects.get(
                internal_ips_set__id=ip_id
            )
            resource = marketplace_models.Resource.objects.get(scope=scope)
        except ObjectDoesNotExist:
            logger.debug(
                'Skipping resource synchronization for OpenStack instance '
                'because marketplace resource does not exist. '
                'Resource ID: %s',
                ip_id,
            )
            continue

        utils.import_instance_metadata(resource)


def import_instance_metadata(vm):
    if not vm:
        return
    try:
        resource = marketplace_models.Resource.objects.get(scope=vm)
    # AttributeError can be raised by generic foreign key, WAL-2662
    except (ObjectDoesNotExist, AttributeError):
        logger.debug(
            'Skipping resource synchronization for OpenStack instance '
            'because marketplace resource does not exist. '
            'Virtual machine ID: %s',
            vm.id,
        )
    else:
        utils.import_instance_metadata(resource)


def synchronize_internal_ips_on_delete(sender, instance, **kwargs):
    try:
        vm = instance.instance
    except ObjectDoesNotExist:
        pass
    else:
        import_instance_metadata(vm)


def synchronize_floating_ips_on_delete(sender, instance, **kwargs):
    if instance.internal_ip:
        import_instance_metadata(instance.internal_ip.instance)


def create_resource_of_volume_if_instance_created(
    sender, instance, created=False, **kwargs
):
    resource = instance

    if (
        not created
        or not resource.scope
        or not getattr(resource.offering, 'scope', None)
    ):
        return

    if resource.offering.type != INSTANCE_TYPE:
        return

    instance = resource.scope

    volume_offering = utils.get_offering(
        VOLUME_TYPE, getattr(resource.offering, 'scope', None)
    )
    if not volume_offering:
        return

    for volume in instance.volumes.all():
        if marketplace_models.Resource.objects.filter(scope=volume).exists():
            continue

        volume_resource = marketplace_models.Resource(
            project=resource.project,
            offering=volume_offering,
            created=resource.created,
            name=volume.name,
            scope=volume,
        )

        volume_resource.init_cost()
        volume_resource.save()
        utils.import_volume_metadata(volume_resource)
        volume_resource.init_quotas()


def create_marketplace_resource_for_imported_resources(
    sender, instance, offering=None, plan=None, **kwargs
):
    resource = marketplace_models.Resource(
        # backend_id is None if instance is being restored from backup because
        # on database level there's uniqueness constraint enforced for backend_id
        # but in marketplace resource backend_is not nullable
        backend_id=instance.backend_id or '',
        project=instance.project,
        state=get_resource_state(instance.state),
        name=instance.name,
        scope=instance,
        created=instance.created,
        plan=plan,
        offering=offering,
    )

    if isinstance(instance, openstack_tenant_models.Instance):
        offering = offering or utils.get_offering(
            INSTANCE_TYPE, instance.service_settings
        )

        if not offering:
            return

        resource.offering = offering

        resource.init_cost()
        resource.save()
        utils.import_instance_metadata(resource)
        resource.init_quotas()

    if isinstance(instance, openstack_tenant_models.Volume):
        offering = offering or utils.get_offering(
            VOLUME_TYPE, instance.service_settings
        )

        if not offering:
            return

        resource.offering = offering

        resource.init_cost()
        resource.save()
        utils.import_volume_metadata(resource)
        resource.init_quotas()

    if isinstance(instance, openstack_models.Tenant):
        offering = offering or utils.get_offering(
            TENANT_TYPE, instance.service_settings
        )

        if not offering:
            return

        resource.offering = offering

        resource.init_cost()
        resource.save()
        utils.import_resource_metadata(resource)
        resource.init_quotas()
        create_offerings_for_volume_and_instance(instance)


def import_resource_metadata_when_resource_is_created(
    sender, instance, created=False, **kwargs
):
    if not created:
        return

    if isinstance(instance.scope, openstack_tenant_models.Volume):
        utils.import_volume_metadata(instance)

    if isinstance(instance.scope, openstack_tenant_models.Instance):
        utils.import_instance_metadata(instance)


def update_openstack_tenant_usages(sender, instance, created=False, **kwargs):
    if created:
        return

    if not isinstance(instance.scope, openstack_models.Tenant):
        return

    tenant = instance.scope

    try:
        resource = marketplace_models.Resource.objects.get(scope=tenant)
    except ObjectDoesNotExist:
        logger.debug(
            'Skipping usages synchronization for tenant because '
            'resource does not exist. OpenStack tenant ID: %s',
            tenant.id,
        )
        return

    utils.import_usage(resource)


def create_offering_component_for_volume_type(
    sender, instance, created=False, **kwargs
):
    volume_type = instance

    try:
        offering = marketplace_models.Offering.objects.get(scope=volume_type.settings)
    except marketplace_models.Offering.DoesNotExist:
        logger.warning(
            'Skipping synchronization of volume type with '
            'marketplace because offering for service settings is not have found. '
            'Settings ID: %s',
            instance.settings.id,
        )
        return

    storage_mode = offering.plugin_options.get('storage_mode') or STORAGE_MODE_FIXED
    if storage_mode == STORAGE_MODE_FIXED:
        logger.debug(
            'Skipping synchronization of volume type with '
            'marketplace because offering has fixed storage mode enabled. '
            'Offering ID: %s',
            offering.id,
        )
        return

    content_type = ContentType.objects.get_for_model(volume_type)

    # It is assumed that article code and product code are filled manually via UI
    marketplace_models.OfferingComponent.objects.update_or_create(
        object_id=volume_type.id,
        content_type=content_type,
        defaults=dict(
            offering=offering,
            name='Storage (%s)' % instance.name,
            # It is expected that internal name of offering component related to volume type
            # matches storage quota name generated in OpenStack
            type='gigabytes_' + instance.name,
            measured_unit='GB',
            description=instance.description,
            billing_type=marketplace_models.OfferingComponent.BillingTypes.LIMIT,
        ),
    )


def delete_offering_component_for_volume_type(sender, instance, **kwargs):
    marketplace_models.OfferingComponent.objects.filter(scope=instance).delete()


def synchronize_limits_when_storage_mode_is_switched(
    sender, instance, created=False, **kwargs
):
    offering = instance

    if created:
        return

    if offering.type != TENANT_TYPE:
        return

    if not offering.tracker.has_changed('plugin_options'):
        return

    old_mode = (offering.tracker.previous('plugin_options') or {}).get('storage_mode')
    new_mode = (offering.plugin_options or {}).get('storage_mode')

    if not new_mode:
        logger.debug(
            'Skipping OpenStack tenant synchronization because new storage mode is not defined'
        )
        return

    if old_mode == new_mode:
        logger.debug(
            'Skipping OpenStack tenant synchronization because storage mode is not changed.'
        )
        return

    logger.info(
        'Synchronizing OpenStack tenant limits because storage mode is switched '
        'from %s to %s',
        (old_mode, new_mode),
    )

    resources = marketplace_models.Resource.objects.filter(offering=offering).exclude(
        state__in=(States.TERMINATED, States.TERMINATING)
    )

    for resource in resources:
        utils.import_limits_when_storage_mode_is_switched(resource)
        utils.import_usage(resource)

        serialized_resource = core_utils.serialize_instance(resource)
        transaction.on_commit(
            lambda: tasks.push_tenant_limits.delay(serialized_resource)
        )


def import_instances_and_volumes_if_tenant_has_been_imported(
    sender, instance, offering=None, plan=None, **kwargs
):
    tenant = instance

    if not (
        marketplace_models.Category.objects.filter(default_vm_category=True).exists()
        and marketplace_models.Category.objects.filter(
            default_volume_category=True
        ).exists()
    ):
        logger.info(
            'An import of instances and volumes is impossible because categories for them are not setted.'
        )
        return

    serialized_resource = core_utils.serialize_instance(tenant)
    transaction.on_commit(
        lambda: tasks.import_instances_and_volumes_of_tenant.delay(serialized_resource)
    )


def synchronize_tenant_name(sender, instance, offering=None, plan=None, **kwargs):
    tenant = instance

    if not instance.tracker.has_changed('name'):
        return

    service_settings = structure_models.ServiceSettings.objects.filter(
        object_id=tenant.id, content_type=ContentType.objects.get_for_model(tenant)
    )

    for ss in service_settings:
        offerings = marketplace_models.Offering.objects.filter(
            object_id=ss.id, content_type=ContentType.objects.get_for_model(ss)
        )

        for offering in offerings.filter(type=INSTANCE_TYPE):
            offering.name = utils.get_offering_name_for_instance(tenant)
            offering.save(update_fields=['name'])

        for offering in offerings.filter(type=VOLUME_TYPE):
            offering.name = utils.get_offering_name_for_volume(tenant)
            offering.save(update_fields=['name'])
