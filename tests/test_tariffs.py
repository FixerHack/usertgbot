"""Tariff definitions and command gating."""

from shared.tariffs import (
    PLANS,
    Tariff,
    get_plan,
    purchasable_plans,
    tariff_grants_command,
)


def test_all_tariffs_defined():
    assert set(PLANS) == {Tariff.STANDARD, Tariff.PRO, Tariff.PREMIUM}


def test_quotas():
    assert get_plan(Tariff.STANDARD).check_quota == 5
    assert get_plan(Tariff.PRO).check_quota == 10


def test_profit_targets():
    assert get_plan(Tariff.STANDARD).profit_uah == 100
    assert get_plan(Tariff.PRO).profit_uah == 200


def test_premium_not_purchasable():
    assert get_plan(Tariff.PREMIUM).available is False
    ids = {p.id for p in purchasable_plans()}
    assert ids == {"standard", "pro"}


def test_get_plan_accepts_string():
    assert get_plan("pro").tariff is Tariff.PRO


def test_command_gating():
    # base commands on any paid tier
    for cmd in ("info", "me", "ban", "check"):
        assert tariff_grants_command(Tariff.STANDARD, cmd)
        assert tariff_grants_command(Tariff.PRO, cmd)
    # autoresponder is Pro-only
    assert not tariff_grants_command(Tariff.STANDARD, "autoresponder")
    assert tariff_grants_command(Tariff.PRO, "autoresponder")
    # premium grants nothing while unavailable
    assert not tariff_grants_command(Tariff.PREMIUM, "info")
