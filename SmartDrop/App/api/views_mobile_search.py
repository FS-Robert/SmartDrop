from rest_framework.response import Response
from rest_framework.views import APIView

from ..queries import is_admin
from ..search_sections import matching_sections
from .permissions import IsAuthenticatedUser


class MobileBusquedaGlobalView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        search_term = request.query_params.get('q', '').strip().casefold()
        return Response({'ok': True, 'secciones': matching_sections(search_term, is_admin(request.user))})
