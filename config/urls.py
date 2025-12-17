from django.conf import settings
from django.conf.urls.static import static

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
                  path('', include('services.urls')),
                  path('xt9a7p_admin_portal_443/', admin.site.urls),
                  path('accounts/', include('accounts.urls'))
              ] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
