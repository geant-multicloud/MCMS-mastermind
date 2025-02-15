from django.db import transaction
from django.utils.translation import ugettext_lazy as _
from rest_framework import decorators, response
from rest_framework import serializers as rf_serializers
from rest_framework import status, viewsets

from waldur_core.core import validators as core_validators
from waldur_core.structure import views as structure_views

from . import executors, filters, models, serializers


class ImageViewSet(structure_views.BaseServicePropertyViewSet):
    queryset = models.Image.objects.all()
    serializer_class = serializers.ImageSerializer
    filterset_class = filters.ImageFilter
    lookup_field = 'uuid'

    def get_queryset(self):
        return models.Image.objects.order_by('name')


class SizeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.Size.objects.all()
    serializer_class = serializers.SizeSerializer
    filterset_class = filters.SizeFilter
    lookup_field = 'uuid'


class LocationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.Location.objects.filter(enabled=True)
    serializer_class = serializers.LocationSerializer
    filterset_class = filters.LocationFilter
    lookup_field = 'uuid'


class ResourceGroupViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.ResourceGroup.objects.all().order_by('name')
    serializer_class = serializers.ResourceGroupSerializer
    lookup_field = 'uuid'


class PublicIPViewSet(structure_views.ResourceViewSet):
    queryset = models.PublicIP.objects.all().order_by('name')
    filterset_class = filters.PublicIPFilter
    serializer_class = serializers.PublicIPSerializer
    create_executor = executors.PublicIPCreateExecutor
    delete_executor = executors.PublicIPDeleteExecutor


class VirtualMachineViewSet(structure_views.ResourceViewSet):
    queryset = models.VirtualMachine.objects.all().order_by('name')
    filterset_class = filters.VirtualMachineFilter
    serializer_class = serializers.VirtualMachineSerializer
    create_executor = executors.VirtualMachineCreateExecutor
    delete_executor = executors.VirtualMachineDeleteExecutor
    pull_executor = executors.VirtualMachinePullExecutor

    @decorators.action(detail=True, methods=['post'])
    def start(self, request, uuid=None):
        virtual_machine = self.get_object()
        executors.VirtualMachineStartExecutor().execute(virtual_machine)
        return response.Response(
            {'status': _('start was scheduled')}, status=status.HTTP_202_ACCEPTED
        )

    start_validators = [
        core_validators.StateValidator(models.VirtualMachine.States.OK),
        core_validators.RuntimeStateValidator('stopped'),
    ]
    start_serializer_class = rf_serializers.Serializer

    @decorators.action(detail=True, methods=['post'])
    def stop(self, request, uuid=None):
        virtual_machine = self.get_object()
        executors.VirtualMachineStopExecutor().execute(virtual_machine)
        return response.Response(
            {'status': _('stop was scheduled')}, status=status.HTTP_202_ACCEPTED
        )

    stop_validators = [
        core_validators.StateValidator(models.VirtualMachine.States.OK),
        core_validators.RuntimeStateValidator('running'),
    ]
    stop_serializer_class = rf_serializers.Serializer

    @decorators.action(detail=True, methods=['post'])
    def restart(self, request, uuid=None):
        virtual_machine = self.get_object()
        executors.VirtualMachineRestartExecutor().execute(virtual_machine)
        return response.Response(
            {'status': _('restart was scheduled')}, status=status.HTTP_202_ACCEPTED
        )

    restart_validators = [
        core_validators.StateValidator(models.VirtualMachine.States.OK),
        core_validators.RuntimeStateValidator('running'),
    ]
    restart_serializer_class = rf_serializers.Serializer


class SQLServerViewSet(structure_views.ResourceViewSet):
    queryset = models.SQLServer.objects.all().order_by('name')
    filterset_class = filters.SQLServerFilter
    serializer_class = serializers.SQLServerSerializer
    create_executor = executors.SQLServerCreateExecutor
    delete_executor = executors.SQLServerDeleteExecutor

    @decorators.action(detail=True, methods=['post'])
    def create_database(self, request, uuid=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        database = serializer.save()

        transaction.on_commit(
            lambda: executors.SQLDatabaseCreateExecutor().execute(database)
        )

        payload = {
            'status': _('SQL database creation was scheduled'),
            'database_uuid': database.uuid.hex,
        }
        return response.Response(payload, status=status.HTTP_202_ACCEPTED)

    create_database_validators = [
        core_validators.StateValidator(models.SQLServer.States.OK)
    ]
    create_database_serializer_class = serializers.SQLDatabaseCreateSerializer


class SQLDatabaseViewSet(structure_views.ResourceViewSet):
    queryset = models.SQLDatabase.objects.all().order_by('name')
    filterset_class = filters.SQLDatabaseFilter
    serializer_class = serializers.SQLDatabaseSerializer
    create_executor = executors.SQLDatabaseCreateExecutor
    delete_executor = executors.SQLDatabaseDeleteExecutor
