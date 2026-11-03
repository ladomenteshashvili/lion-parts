"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    path("api/", include("api.urls")),
    path("api/", include("accounts.urls")),
    # Legacy session_id routes (accounts/parts/cart/orders urls) are not mounted: see api/legacy.py.
]

# Demo routes exist only when enabled (staging/tests); in production they aren't resolvable at all.
if settings.ENABLE_DEMO_ENDPOINTS:
    urlpatterns.append(path("api/demo/", include("api.demo_urls")))

# static() returns no patterns unless DEBUG is on; in production nginx serves /media/.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# JSON error envelope outside DRF views too (G§4.4).
handler404 = "api.views.not_found"
handler500 = "api.views.server_error"
