import logging
from urllib.parse import urlparse

from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import ugettext_lazy as _
from model_utils import FieldTracker
from model_utils.models import TimeStampedModel

from waldur_core.core import models as core_models
from waldur_core.core.fields import JSONField
from waldur_core.logging.loggers import LoggableMixin
from waldur_core.quotas import models as quotas_models
from waldur_core.structure import models as structure_models
from waldur_core.structure import utils as structure_utils
from waldur_geo_ip.utils import get_coordinates_by_ip
from waldur_openstack.openstack import models as openstack_models
from waldur_openstack.openstack_base import models as openstack_base_models

logger = logging.getLogger(__name__)


TenantQuotas = openstack_models.Tenant.Quotas


class Flavor(LoggableMixin, structure_models.ServiceProperty):
    cores = models.PositiveSmallIntegerField(help_text=_('Number of cores in a VM'))
    ram = models.PositiveIntegerField(help_text=_('Memory size in MiB'))
    disk = models.PositiveIntegerField(help_text=_('Root disk size in MiB'))

    class Meta:
        unique_together = ('settings', 'backend_id')

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-flavor'

    @classmethod
    def get_backend_fields(cls):
        return super(Flavor, cls).get_backend_fields() + ('cores', 'ram', 'disk')


class Image(openstack_base_models.BaseImage):
    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-image'


class SecurityGroup(core_models.DescribableMixin, structure_models.ServiceProperty):
    class Meta:
        unique_together = ('settings', 'backend_id')

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-sgp'


class SecurityGroupRule(openstack_base_models.BaseSecurityGroupRule):
    class Meta:
        unique_together = ('security_group', 'backend_id')

    security_group = models.ForeignKey(
        on_delete=models.CASCADE, to=SecurityGroup, related_name='rules'
    )
    remote_group = models.ForeignKey(
        on_delete=models.CASCADE,
        to=SecurityGroup,
        related_name='+',
        null=True,
        blank=True,
    )


class TenantQuotaMixin(quotas_models.SharedQuotaMixin):
    """
    It allows to update both service settings and shared tenant quotas.
    """

    def get_quota_scopes(self):
        service_settings = self.service_settings
        return service_settings, service_settings.scope


class FloatingIP(core_models.LoggableMixin, structure_models.ServiceProperty):
    address = models.GenericIPAddressField(protocol='IPv4', null=True, default=None)
    runtime_state = models.CharField(max_length=30)
    backend_network_id = models.CharField(max_length=255, editable=False)
    is_booked = models.BooleanField(
        default=False,
        help_text=_('Marks if floating IP has been booked for provisioning.'),
    )
    internal_ip = models.ForeignKey(
        'InternalIP', related_name='floating_ips', null=True, on_delete=models.SET_NULL
    )
    tracker = FieldTracker()

    class Meta:
        unique_together = ('settings', 'address')
        verbose_name = _('Floating IP')
        verbose_name_plural = _('Floating IPs')

    def __str__(self):
        return '%s:%s | %s' % (self.address, self.runtime_state, self.settings)

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-fip'

    def get_backend(self):
        return self.settings.get_backend()

    def increase_backend_quotas_usage(self, validate=True):
        self.settings.add_quota_usage(
            self.settings.Quotas.floating_ip_count, 1, validate=validate
        )

    def decrease_backend_quotas_usage(self):
        self.settings.add_quota_usage(self.settings.Quotas.floating_ip_count, -1)

    @classmethod
    def get_backend_fields(cls):
        return super(FloatingIP, cls).get_backend_fields() + (
            'address',
            'runtime_state',
            'backend_network_id',
        )

    def get_log_fields(self):
        return ('address', 'runtime_state', 'backend_id', 'backend_network_id')


class Volume(TenantQuotaMixin, structure_models.Volume):
    # backend_id is nullable on purpose, otherwise
    # it wouldn't be possible to put a unique constraint on it
    backend_id = models.CharField(max_length=255, blank=True, null=True)

    instance = models.ForeignKey(
        on_delete=models.CASCADE,
        to='Instance',
        related_name='volumes',
        blank=True,
        null=True,
    )
    device = models.CharField(
        max_length=50,
        blank=True,
        validators=[
            RegexValidator(
                '^/dev/[a-zA-Z0-9]+$',
                message=_('Device should match pattern "/dev/alphanumeric+"'),
            )
        ],
        help_text=_('Name of volume as instance device e.g. /dev/vdb.'),
    )
    bootable = models.BooleanField(default=False)
    metadata = JSONField(blank=True)
    image = models.ForeignKey(Image, blank=True, null=True, on_delete=models.SET_NULL)
    image_name = models.CharField(max_length=150, blank=True)
    image_metadata = JSONField(blank=True)
    type: 'VolumeType' = models.ForeignKey(
        'VolumeType', blank=True, null=True, on_delete=models.SET_NULL
    )
    availability_zone = models.ForeignKey(
        'VolumeAvailabilityZone', blank=True, null=True, on_delete=models.SET_NULL
    )
    source_snapshot = models.ForeignKey(
        'Snapshot',
        related_name='volumes',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    # TODO: Move this fields to resource model.
    action = models.CharField(max_length=50, blank=True)
    action_details = JSONField(default=dict)

    tracker = FieldTracker()

    class Meta:
        unique_together = ('service_settings', 'backend_id')

    def get_quota_deltas(self):
        deltas = {
            TenantQuotas.volumes: 1,
            TenantQuotas.volumes_size: self.size,
            TenantQuotas.storage: self.size,
        }
        if self.type:
            deltas['gigabytes_' + self.type.name] = self.size / 1024
        return deltas

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-volume'

    @classmethod
    def get_backend_fields(cls):
        return super(Volume, cls).get_backend_fields() + (
            'name',
            'description',
            'size',
            'metadata',
            'type',
            'bootable',
            'runtime_state',
            'device',
            'instance',
            'availability_zone',
            'image',
            'image_metadata',
            'image_name',
        )


class Snapshot(TenantQuotaMixin, structure_models.Snapshot):
    # backend_id is nullable on purpose, otherwise
    # it wouldn't be possible to put a unique constraint on it
    backend_id = models.CharField(max_length=255, blank=True, null=True)

    source_volume: Volume = models.ForeignKey(
        Volume, related_name='snapshots', null=True, on_delete=models.PROTECT
    )
    metadata = JSONField(blank=True)
    # TODO: Move this fields to resource model.
    action = models.CharField(max_length=50, blank=True)
    action_details = JSONField(default=dict)
    snapshot_schedule = models.ForeignKey(
        'SnapshotSchedule',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='snapshots',
    )

    tracker = FieldTracker()

    kept_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('Guaranteed time of snapshot retention. If null - keep forever.'),
    )

    class Meta:
        unique_together = ('service_settings', 'backend_id')

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-snapshot'

    def get_quota_deltas(self):
        deltas = {
            TenantQuotas.snapshots: 1,
            TenantQuotas.snapshots_size: self.size,
        }
        return deltas

    @classmethod
    def get_backend_fields(cls):
        return super(Snapshot, cls).get_backend_fields() + (
            'name',
            'description',
            'size',
            'metadata',
            'source_volume',
            'runtime_state',
        )


class SnapshotRestoration(core_models.UuidMixin, TimeStampedModel):
    snapshot = models.ForeignKey(
        on_delete=models.CASCADE, to=Snapshot, related_name='restorations'
    )
    volume = models.OneToOneField(
        Volume, related_name='restoration', on_delete=models.CASCADE
    )

    class Permissions:
        customer_path = 'snapshot__project__customer'
        project_path = 'snapshot__project'


class InstanceAvailabilityZone(structure_models.BaseServiceProperty):
    settings = models.ForeignKey(
        on_delete=models.CASCADE, to=structure_models.ServiceSettings, related_name='+'
    )
    available = models.BooleanField(default=True)

    class Meta:
        unique_together = ('settings', 'name')

    def __str__(self):
        return self.name

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-instance-availability-zone'


class Instance(TenantQuotaMixin, structure_models.VirtualMachine):
    class RuntimeStates:
        # All possible OpenStack Instance states on backend.
        # See https://docs.openstack.org/developer/nova/vmstates.html
        ACTIVE = 'ACTIVE'
        BUILDING = 'BUILDING'
        DELETED = 'DELETED'
        SOFT_DELETED = 'SOFT_DELETED'
        ERROR = 'ERROR'
        UNKNOWN = 'UNKNOWN'
        HARD_REBOOT = 'HARD_REBOOT'
        REBOOT = 'REBOOT'
        REBUILD = 'REBUILD'
        PASSWORD = 'PASSWORD'
        PAUSED = 'PAUSED'
        RESCUED = 'RESCUED'
        RESIZED = 'RESIZED'
        REVERT_RESIZE = 'REVERT_RESIZE'
        SHUTOFF = 'SHUTOFF'
        STOPPED = 'STOPPED'
        SUSPENDED = 'SUSPENDED'
        VERIFY_RESIZE = 'VERIFY_RESIZE'

    # backend_id is nullable on purpose, otherwise
    # it wouldn't be possible to put a unique constraint on it
    backend_id = models.CharField(max_length=255, blank=True, null=True)

    availability_zone = models.ForeignKey(
        InstanceAvailabilityZone, blank=True, null=True, on_delete=models.SET_NULL
    )
    flavor_name = models.CharField(max_length=255, blank=True)
    flavor_disk = models.PositiveIntegerField(
        default=0, help_text=_('Flavor disk size in MiB')
    )
    security_groups = models.ManyToManyField(SecurityGroup, related_name='instances')
    # TODO: Move this fields to resource model.
    action = models.CharField(max_length=50, blank=True)
    action_details = JSONField(default=dict)
    subnets = models.ManyToManyField('SubNet', through='InternalIP')

    tracker = FieldTracker()

    class Meta:
        unique_together = ('service_settings', 'backend_id')
        ordering = ['name', 'created']

    @property
    def external_ips(self):
        return list(self.floating_ips.values_list('address', flat=True))

    @property
    def internal_ips(self):
        return [
            val['ip_address']
            for ip_list in self.internal_ips_set.values_list('fixed_ips', flat=True)
            for val in ip_list
        ]

    @property
    def size(self):
        return self.volumes.aggregate(models.Sum('size'))['size__sum']

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-instance'

    def get_log_fields(self):
        return (
            'uuid',
            'name',
            'type',
            'service_settings',
            'project',
            'ram',
            'cores',
        )

    def detect_coordinates(self):
        settings = self.service_settings
        options = settings.options or {}
        if 'latitude' in options and 'longitude' in options:
            return structure_utils.Coordinates(
                latitude=settings['latitude'], longitude=settings['longitude']
            )
        else:
            hostname = urlparse(settings.backend_url).hostname
            if hostname:
                return get_coordinates_by_ip(hostname)

    def get_quota_deltas(self):
        return {
            TenantQuotas.instances: 1,
            TenantQuotas.ram: self.ram,
            TenantQuotas.vcpu: self.cores,
        }

    @property
    def floating_ips(self):
        return FloatingIP.objects.filter(internal_ip__instance=self)

    @classmethod
    def get_backend_fields(cls):
        return super(Instance, cls).get_backend_fields() + (
            'flavor_name',
            'flavor_disk',
            'ram',
            'cores',
            'disk',
            'runtime_state',
            'availability_zone',
        )

    @classmethod
    def get_online_state(cls):
        return Instance.RuntimeStates.ACTIVE

    @classmethod
    def get_offline_state(cls):
        return Instance.RuntimeStates.SHUTOFF


class Backup(structure_models.SubResource):
    instance = models.ForeignKey(
        Instance, related_name='backups', on_delete=models.PROTECT
    )
    backup_schedule = models.ForeignKey(
        'BackupSchedule',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='backups',
    )
    kept_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('Guaranteed time of backup retention. If null - keep forever.'),
    )
    metadata = JSONField(
        blank=True,
        help_text=_(
            'Additional information about backup, can be used for backup restoration or deletion'
        ),
    )
    snapshots = models.ManyToManyField('Snapshot', related_name='backups')

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-backup'


class BackupRestoration(core_models.UuidMixin, TimeStampedModel):
    """ This model corresponds to instance restoration from backup. """

    backup = models.ForeignKey(
        on_delete=models.CASCADE, to=Backup, related_name='restorations'
    )
    instance = models.OneToOneField(
        Instance, related_name='+', on_delete=models.CASCADE
    )
    flavor = models.ForeignKey(
        Flavor, related_name='+', null=True, blank=True, on_delete=models.SET_NULL
    )

    class Permissions:
        customer_path = 'backup__project__customer'
        project_path = 'backup__project'


class BaseSchedule(structure_models.BaseResource, core_models.ScheduleMixin):
    retention_time = models.PositiveIntegerField(
        help_text=_('Retention time in days, if 0 - resource will be kept forever')
    )
    maximal_number_of_resources = models.PositiveSmallIntegerField()
    call_count = models.PositiveSmallIntegerField(
        default=0, help_text=_('How many times a resource schedule was called.')
    )

    class Meta:
        abstract = True


class BackupSchedule(BaseSchedule):
    instance = models.ForeignKey(
        on_delete=models.CASCADE, to=Instance, related_name='backup_schedules'
    )

    tracker = FieldTracker()

    def __str__(self):
        return 'BackupSchedule of %s. Active: %s' % (self.instance, self.is_active)

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-backup-schedule'


class SnapshotSchedule(BaseSchedule):
    source_volume = models.ForeignKey(
        on_delete=models.CASCADE, to=Volume, related_name='snapshot_schedules'
    )

    tracker = FieldTracker()

    def __str__(self):
        return 'SnapshotSchedule of %s. Active: %s' % (
            self.source_volume,
            self.is_active,
        )

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-snapshot-schedule'


class Network(core_models.DescribableMixin, structure_models.ServiceProperty):
    is_external = models.BooleanField(default=False)
    type = models.CharField(max_length=50, blank=True)
    segmentation_id = models.IntegerField(null=True)

    class Meta:
        unique_together = ('settings', 'backend_id')

    def __str__(self):
        return self.name

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-network'


class SubNet(
    openstack_base_models.BaseSubNet,
    core_models.DescribableMixin,
    structure_models.ServiceProperty,
):
    network = models.ForeignKey(
        on_delete=models.CASCADE, to=Network, related_name='subnets'
    )

    class Meta:
        verbose_name = _('Subnet')
        verbose_name_plural = _('Subnets')
        unique_together = ('settings', 'backend_id')

    def __str__(self):
        return '%s (%s)' % (self.name, self.cidr)

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-subnet'


class InternalIP(openstack_base_models.Port):
    """
    Instance may have several IP addresses in the same subnet
    if shared IPs are implemented using Virtual Router Redundancy Protocol.
    """

    # Name "internal_ips" is reserved by virtual machine mixin and corresponds to list of internal IPs.
    # So another related name should be used.
    instance = models.ForeignKey(
        on_delete=models.CASCADE,
        to=Instance,
        related_name='internal_ips_set',
        null=True,
    )
    subnet = models.ForeignKey(
        on_delete=models.CASCADE, to=SubNet, related_name='internal_ips'
    )

    # backend_id is nullable on purpose, otherwise
    # it wouldn't be possible to put a unique constraint on it
    backend_id = models.CharField(max_length=255, null=True)
    settings = models.ForeignKey(
        on_delete=models.CASCADE, to=structure_models.ServiceSettings, related_name='+'
    )
    tracker = FieldTracker()

    def __str__(self):
        # This method should not return None
        return self.backend_id or f'InternalIP <{self.id}>'

    class Meta:
        unique_together = ('backend_id', 'settings')


class VolumeType(openstack_base_models.BaseVolumeType):

    is_default = models.BooleanField(default=False)

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-volume-type'


class VolumeAvailabilityZone(structure_models.BaseServiceProperty):
    settings = models.ForeignKey(
        on_delete=models.CASCADE, to=structure_models.ServiceSettings, related_name='+'
    )
    available = models.BooleanField(default=True)

    class Meta:
        unique_together = ('settings', 'name')

    def __str__(self):
        return self.name

    @classmethod
    def get_url_name(cls):
        return 'openstacktenant-volume-availability-zone'
