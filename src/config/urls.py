from django.urls import path
from ninja_extra import NinjaExtraAPI


api = NinjaExtraAPI()

urlpatterns = [path("api/", api.urls)]
