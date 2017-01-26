from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, permissions, response, status
from rest_framework.decorators import list_route

from nodeconductor.core import views as core_views
from nodeconductor.structure import filters as structure_filters

from . import filters, models, serializers, executors


class PackageTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.PackageTemplate.objects.all()
    serializer_class = serializers.PackageTemplateSerializer
    lookup_field = 'uuid'
    permission_classes = (permissions.IsAuthenticated,)
    filter_backends = (DjangoFilterBackend,)
    filter_class = filters.PackageTemplateFilter


class OpenStackPackageViewSet(core_views.ActionsViewSet):
    queryset = models.OpenStackPackage.objects.all()
    serializer_class = serializers.OpenStackPackageSerializer
    lookup_field = 'uuid'
    filter_backends = (structure_filters.GenericRoleFilter, DjangoFilterBackend)
    filter_class = filters.OpenStackPackageFilter
    disabled_actions = ['update', 'partial_update', 'destroy']

    def create(self, request, *args, **kwargs):
        # package creation is a little bit tricky:
        # We need to use OpenStackPackageCreateSerializer to create package
        # And OpenStackPackageSerializer to display it.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        package = serializer.save()
        executors.OpenStackPackageCreateExecutor.execute(package)

        display_serializer = serializers.OpenStackPackageSerializer(instance=package, context={'request': request})
        return response.Response(display_serializer.data, status=status.HTTP_201_CREATED)

    create_serializer_class = serializers.OpenStackPackageCreateSerializer

    @list_route(methods=['post'])
    def extend(self, request, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_package = serializer.save()
        executors.OpenStackPackageExtendExecutor.execute(new_package.tenant)

        return response.Response({'detail': 'OpenStack package extend has been scheduled'},
                                 status=status.HTTP_202_ACCEPTED)

    extend_serializer_class = serializers.OpenStackPackageExtendSerializer
