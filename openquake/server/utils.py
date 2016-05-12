# -*- coding: utf-8 -*-
# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright (C) 2015-2016 GEM Foundation
#
# OpenQuake is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OpenQuake is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with OpenQuake. If not, see <http://www.gnu.org/licenses/>.

import getpass

from django.conf import settings
from openquake.engine import __version__ as oqversion


def get_user_data(request):
    """
    Returns the real username if authentication support is enabled and user is
    authenticated, otherwise it returns "platform" as user for backward
    compatibility.
    Returns also if the user is 'superuser' or not.
    """

    is_super = False
    if hasattr(request, 'user'):
        if request.user.is_authenticated():
            name = request.user.username
        if request.user.is_superuser:
            is_super = True
    else:
        name = (settings.DEFAULT_USER if
                hasattr(settings, 'DEFAULT_USER') else getpass.getuser())

    return {'name': name, 'is_super': is_super}


def oq_server_context_processor(request):
    """
    A custom context processor which allows injection of additional
    context variables.
    """

    context = {}

    context['oq_engine_server_url'] = ('//' +
                                       request.META.get('HTTP_HOST',
                                                        'localhost:8000'))
    context['oq_engine_version'] = oqversion

    return context