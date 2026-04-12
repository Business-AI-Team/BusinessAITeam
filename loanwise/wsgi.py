"""
WSGI config for LoanWise.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "loanwise.settings")

application = get_wsgi_application()
