"""List pagination (G§4.5): ?page=N, 20 per page -> {count, next, previous, results}."""

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class Pagination(PageNumberPagination):
    page_size = 20

    def get_paginated_response(self, data, **extra):
        """`extra` holds documented aggregates (new_count, group_counts, unread_count), placed after count."""
        return Response({
            "count": self.page.paginator.count,
            **extra,
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "results": data,
        })
