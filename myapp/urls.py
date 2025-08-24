from django.urls import path
from . import views

urlpatterns = [
    path('', views.upload_view, name='home'),
    path('scrap-price/', views.scrap_price, name='scrap_price'),
    path('nearest-dump/', views.nearest_dump, name='nearest_dump'),
    path('api/find_dumpyards/', views.find_dumpyards, name='find_dumpyards'),
    path('faq/', views.faq, name='faq'),
    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
]
