"""Tariff definitions and command gating."""

from shared.tariffs import (
    DURATIONS,
    PLANS,
    Duration,
    Tariff,
    discount_pct,
    effective_profit_uah,
    get_duration,
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
    assert get_plan(Tariff.STANDARD).profit_uah == 50
    assert get_plan(Tariff.PRO).profit_uah == 150


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
    # .send: Standard doesn't have it; Pro does (Premium would too, but it's
    # gated off entirely below since the whole tier isn't purchasable yet)
    assert not tariff_grants_command(Tariff.STANDARD, "send")
    assert tariff_grants_command(Tariff.PRO, "send")
    # premium grants nothing while unavailable, even though its plan data
    # (send_cooldown_seconds etc.) is already filled in for when it launches
    assert not tariff_grants_command(Tariff.PREMIUM, "info")
    assert not tariff_grants_command(Tariff.PREMIUM, "send")


def test_send_cooldown_and_limits_per_tariff():
    pro, premium = get_plan(Tariff.PRO), get_plan(Tariff.PREMIUM)
    assert pro.send_cooldown_seconds == 10 * 60
    assert pro.send_max_count == 50
    # Premium is faster/bigger than Pro, even though it isn't purchasable yet
    assert premium.send_cooldown_seconds == 2 * 60
    assert premium.send_max_count == 100
    assert premium.send_cooldown_seconds < pro.send_cooldown_seconds
    assert premium.send_max_count > pro.send_max_count


def test_durations_defined():
    assert set(DURATIONS) == {Duration.MONTH, Duration.QUARTER, Duration.YEAR}
    assert get_duration("1m").days == 30
    assert get_duration("3m").days == 90
    assert get_duration("1y").days == 365


def test_duration_discount_pct():
    # month/quarter charge exactly their nominal multiplier -> no discount
    assert discount_pct(get_duration(Duration.MONTH)) == 0
    assert discount_pct(get_duration(Duration.QUARTER)) == 0
    # year charges x10 instead of the "fair" x12 -> ~17% off
    assert discount_pct(get_duration(Duration.YEAR)) == 17


def test_effective_profit_no_referral_discount_unchanged():
    month, quarter, year = (get_duration(d) for d in (Duration.MONTH, Duration.QUARTER, Duration.YEAR))
    assert effective_profit_uah(100, month, 0) == 100
    assert effective_profit_uah(100, quarter, 0) == 300
    assert effective_profit_uah(100, year, 0) == 1000  # x10, the year's own -17%


def test_effective_profit_referral_and_year_discount_dont_stack():
    year = get_duration(Duration.YEAR)
    # a small referral discount doesn't beat the year's own -17% (x10) ->
    # the year discount alone applies, referral is NOT stacked on top of it
    assert effective_profit_uah(100, year, 10) == 1000
    # a bigger referral discount (30%) beats x10 (=x8.4 nominal) -> that one
    # wins instead, but still only ONE discount, not both combined
    assert effective_profit_uah(100, year, 30) == 100 * 12 * 0.7


def test_effective_profit_referral_applies_normally_without_a_competing_duration_discount():
    month, quarter = get_duration(Duration.MONTH), get_duration(Duration.QUARTER)
    # month/quarter have no built-in discount, so a referral discount just applies
    assert effective_profit_uah(100, month, 20) == 80
    assert effective_profit_uah(100, quarter, 20) == 300 * 0.8
