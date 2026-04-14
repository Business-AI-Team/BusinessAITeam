"""
Conversions de devises (MGA, EUR, MUR) pour comparaisons cohérentes.
Les taux sont configurables (variables d’environnement / settings), valeur indicative.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    pass


def _rate_to_eur(currency: str) -> Decimal:
    """1 unité de ``currency`` = ? EUR (pivot)."""
    c = (currency or "EUR").upper().strip()
    if c == "EUR":
        return Decimal("1")
    # MGA: environ 1 EUR = N Ariary (configurable)
    if c == "MGA":
        mga_per_eur = Decimal(str(getattr(settings, "LOANWISE_FX_MGA_PER_EUR", 4700)))
        if mga_per_eur <= 0:
            mga_per_eur = Decimal("4700")
        return Decimal("1") / mga_per_eur
    # MUR: environ 1 EUR = N roupies
    if c == "MUR":
        mur_per_eur = Decimal(str(getattr(settings, "LOANWISE_FX_MUR_PER_EUR", 49)))
        if mur_per_eur <= 0:
            mur_per_eur = Decimal("49")
        return Decimal("1") / mur_per_eur
    # fallback: no conversion
    return Decimal("1")


def primary_currency_for_country(country_alpha2: str | None) -> str | None:
    """
    Devise « locale » indicative pour l’affichage (revenu estimé depuis les bulletins).
    Si None, l’UI utilise uniquement ``Customer.income_currency``.
    """
    c = (country_alpha2 or "").strip().upper()[:2]
    if c == "MG":
        return "MGA"
    if c == "MU":
        return "MUR"
    return None


def convert_amount(amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
    """Convertit un montant entre deux devises supportées (via EUR)."""
    if from_currency.upper() == to_currency.upper():
        return amount.quantize(Decimal("0.01"))
    a = amount * _rate_to_eur(from_currency)
    to_eur_inv = _rate_to_eur(to_currency)
    if to_eur_inv <= 0:
        return amount
    out = a / to_eur_inv
    return out.quantize(Decimal("0.01"))


def format_money(amount: Decimal | float | int, currency: str) -> str:
    """Affichage avec code devise explicite."""
    try:
        d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except Exception:
        d = Decimal("0")
    c = (currency or "EUR").upper()
    s = f"{d:,.2f}".replace(",", " ")
    return f"{s} {c}".strip()
