"""/subscribe: a tariff carousel + payment (Telegram Stars or Crypto Pay).

Flow: browse tariffs (◀️/▶️) → «Купити» → a duration×price grid (Місяць/3
місяці/Рік columns, ⭐/$ rows) → tapping a ⭐ or $ cell fires the invoice for
that duration on that channel directly, no separate method-choice step.
• Stars: send_invoice(XTR); pre_checkout answered ok; successful_payment activates.
• Crypto: Crypto Pay invoice + a «Перевірити оплату» button that polls status.
The tariff is held as a PENDING subscription until payment is confirmed.

Prices are computed live (shared.pricing.compute_prices) from each plan's
fixed profit_uah target, grossed up per channel so profit never drops below
that target regardless of the live exchange rate or that channel's fee.
Duration multiplies profit_uah before grossing up (shared.tariffs.DURATIONS);
a referral discount (shared.referrals), if any, multiplies it down further.
"""

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from connect_web.server import connect_server
from db.session import get_session
from management_bot import keyboards, storage, subscriptions
from management_bot.config import settings
from management_bot.payment import build_wayforpay
from management_bot.payment.crypto_pay import CryptoPayProvider
from pay_web.app import REGULAR_MODE_BY_DAYS
from shared import referrals
from shared.i18n import features, lang_of, t
from shared.pricing import PriceBreakdown, compute_prices, get_usd_uah_rate
from shared.tariffs import DURATIONS, PLANS, Duration, Tariff, discount_pct, effective_profit_uah, get_duration, get_plan

logger = logging.getLogger(__name__)
router = Router(name="subscribe")

TARIFF_ORDER = list(PLANS.keys())

# Optional per-tariff image: assets/tariffs/<tariff-id>.(jpg|jpeg|png|webp).
# Missing file -> falls back to a text-only card, so images are fully optional.
ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets" / "tariffs"
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def _tariff_image(tariff_id: str) -> Path | None:
    for suffix in _IMAGE_SUFFIXES:
        path = ASSETS_DIR / f"{tariff_id}{suffix}"
        if path.exists():
            return path
    return None


async def _success_text(db, telegram_id: int, lang: str, title: str) -> str:
    """The "now connect your account" nudge is noise for someone who already
    has one connected — worse, it points at ⚙️ Налаштування where the button
    says the opposite ("Відв'язати")."""
    status = await storage.get_user_status(db, telegram_id)
    text = t(lang, "sub_success", title=title)
    if not status.sessions:
        text = f"{text}\n{t(lang, 'sub_connect_hint')}"
    return text


def _crypto() -> CryptoPayProvider | None:
    if not settings.crypto_pay_token:
        return None
    return CryptoPayProvider(
        settings.crypto_pay_token, testnet=settings.crypto_pay_testnet, fiat=settings.crypto_pay_fiat
    )


# --- carousel --------------------------------------------------------------


def _card_text(plan, prices: PriceBreakdown | None, lang: str) -> str:
    lines = [f"💳 <b>{plan.title}</b>"]
    if plan.available and prices is not None:
        # uah_invoice, not profit_uah: this is a price the user can actually
        # pay by card, so it has to be the sum the card is charged.
        lines.append(f"{prices.uah_invoice}₴  ·  ${prices.usd_net}  ·  {prices.stars}⭐")
    lines.append("")
    lines.extend(f"• {f}" for f in features(lang, plan.id))
    return "\n".join(lines)


def _carousel_kb(idx: int, lang: str) -> InlineKeyboardMarkup:
    plan = PLANS[TARIFF_ORDER[idx]]
    prev_i = (idx - 1) % len(TARIFF_ORDER)
    next_i = (idx + 1) % len(TARIFF_ORDER)
    mid = (
        InlineKeyboardButton(text=t(lang, "sub_buy"), callback_data=f"sub:buy:{plan.id}")
        if plan.available
        else InlineKeyboardButton(text=t(lang, "sub_in_dev_btn"), callback_data="sub:nop")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀️", callback_data=f"sub:nav:{prev_i}"),
                mid,
                InlineKeyboardButton(text="▶️", callback_data=f"sub:nav:{next_i}"),
            ]
        ]
    )


_DURATION_LABEL_KEYS = {
    Duration.MONTH: "sub_duration_month",
    Duration.QUARTER: "sub_duration_3months",
    Duration.YEAR: "sub_duration_year",
}


async def _duration_prices(plan, discount: int) -> dict[Duration, PriceBreakdown]:
    """Price breakdown for every duration option, with `discount` (%, from a
    referral link) applied on top of the period's own multiplier."""
    rate = await get_usd_uah_rate()
    out: dict[Duration, PriceBreakdown] = {}
    for code, opt in DURATIONS.items():
        effective = effective_profit_uah(plan.profit_uah, opt, discount)
        out[code] = await compute_prices(effective, usd_uah_rate=rate)
    return out


def _duration_kb(tariff_id: str, idx: int, lang: str, prices: dict[Duration, PriceBreakdown]) -> InlineKeyboardMarkup:
    """A 4x3 grid: one column per duration. The label and ₴ rows are inert
    (₴ isn't an actual payment channel, just a reference figure) — only the
    ⭐ and $ rows are real, tapping either fires the invoice for that
    duration on that channel directly (no separate method-choice step)."""
    codes = list(DURATIONS.items())

    label_row = []
    for code, opt in codes:
        label = t(lang, _DURATION_LABEL_KEYS[code])
        pct = discount_pct(opt)
        text = f"{label} −{pct}%" if pct > 0 else label
        label_row.append(InlineKeyboardButton(text=text, callback_data="sub:noop"))

    # Hryvnia leads: it is the currency the product is priced in and the one
    # most buyers here actually hold. It used to be an inert reference row —
    # now it charges a card, so it sits at the top of the grid.
    uah_row = [
        InlineKeyboardButton(
            text=f"{prices[code].uah_invoice}₴", callback_data=f"sub:pay:{tariff_id}:{code.value}:card"
        )
        for code, _ in codes
    ]
    stars_row = [
        InlineKeyboardButton(text=f"{prices[code].stars}⭐", callback_data=f"sub:pay:{tariff_id}:{code.value}:stars")
        for code, _ in codes
    ]
    usd_row = [
        InlineKeyboardButton(text=f"${prices[code].usd_net}", callback_data=f"sub:pay:{tariff_id}:{code.value}:crypto")
        for code, _ in codes
    ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            label_row,
            uah_row,
            stars_row,
            usd_row,
            [InlineKeyboardButton(text=t(lang, "sub_back"), callback_data=f"sub:nav:{idx}")],
        ]
    )


async def _edit(message: Message, text: str, *, kb: InlineKeyboardMarkup | None = None) -> None:
    """edit_text fails on a photo message (needs edit_caption instead) — the
    tariff card may be either, so pick the right call."""
    if message.photo:
        await message.edit_caption(caption=text, reply_markup=kb)
    else:
        await message.edit_text(text, reply_markup=kb)


async def _send_card(message: Message, idx: int, lang: str, *, text: str | None = None, kb: InlineKeyboardMarkup | None = None) -> None:
    """Send a tariff card as a photo (if assets/tariffs/<id>.* exists) or text."""
    plan = PLANS[TARIFF_ORDER[idx]]
    if text is not None:
        body, markup = text, (kb if kb is not None else _carousel_kb(idx, lang))
    else:
        prices = await compute_prices(plan.profit_uah) if plan.available else None
        body, markup = _card_text(plan, prices, lang), (kb if kb is not None else _carousel_kb(idx, lang))
    image = _tariff_image(plan.id)
    if image is not None:
        await message.answer_photo(
            BufferedInputFile(image.read_bytes(), filename=image.name), caption=body, reply_markup=markup
        )
    else:
        await message.answer(body, reply_markup=markup)


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    await _send_card(message, 0, lang_of(message))


@router.callback_query(F.data == "sub:nop")
async def on_nop(callback: CallbackQuery) -> None:
    await callback.answer(t(lang_of(callback), "sub_in_dev_alert"), show_alert=True)


@router.callback_query(F.data == "sub:noop")
async def on_noop(callback: CallbackQuery) -> None:
    """The duration-grid's label/₴ rows — visual only, no action."""
    await callback.answer()


@router.callback_query(F.data.startswith("sub:nav:"))
async def on_nav(callback: CallbackQuery) -> None:
    idx = int(callback.data.rsplit(":", 1)[1]) % len(TARIFF_ORDER)
    await _send_card(callback.message, idx, lang_of(callback))
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data.startswith("sub:buy:"))
async def on_buy(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    tariff_id = callback.data.rsplit(":", 1)[1]
    plan = get_plan(tariff_id)
    if not plan.available:
        await callback.answer(t(lang, "sub_unavailable"), show_alert=True)
        return
    idx = TARIFF_ORDER.index(Tariff(tariff_id))
    async with get_session() as db:
        discount = await referrals.get_discount_percent(db, callback.from_user.id)
    prices = await _duration_prices(plan, discount)
    text = f"{_card_text(plan, None, lang)}\n\n{t(lang, 'sub_choose_duration')}"
    if discount:
        text += f"\n{t(lang, 'sub_discount_applied', pct=discount)}"
    await _send_card(
        callback.message, idx, lang,
        text=text,
        kb=_duration_kb(tariff_id, idx, lang, prices),
    )
    await callback.message.delete()
    await callback.answer()


# --- Telegram Stars --------------------------------------------------------


async def _grant_free(callback: CallbackQuery, lang: str, plan, tariff_id: str, opt) -> None:
    """A 100% referral discount brings the price to 0 — no payment gateway
    accepts a zero-amount invoice (Crypto Pay outright rejects it), so a
    fully-discounted purchase activates the subscription directly instead."""
    user = callback.from_user
    async with get_session() as db:
        sub = await subscriptions.create_pending(
            db, telegram_id=user.id, tariff=Tariff(tariff_id), provider_name="referral_free",
            username=user.username, full_name=user.full_name, period_days=opt.days,
        )
        await subscriptions.activate(db, sub)
        await db.commit()
        text = await _success_text(db, user.id, lang, plan.title)
    await callback.message.answer(text, reply_markup=keyboards.main_menu(lang))
    await callback.answer()


@router.callback_query(F.data.startswith("sub:pay:") & F.data.endswith(":stars"))
async def on_pay_stars(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    _, _, tariff_id, dur_code, _ = callback.data.split(":")
    plan = get_plan(tariff_id)
    if not plan.available:
        # callback_data isn't trusted input — a crafted callback_query (e.g.
        # via a raw MTProto call, trivial with Telethon) could name any
        # tariff id directly, skipping on_buy's own availability check.
        # Premium's profit_uah=0 would otherwise sail straight through the
        # free-grant path below and hand out a real subscription for free.
        await callback.answer(t(lang, "sub_unavailable"), show_alert=True)
        return
    opt = get_duration(dur_code)
    user = callback.from_user
    async with get_session() as db:
        discount = await referrals.get_discount_percent(db, user.id)
    effective = effective_profit_uah(plan.profit_uah, opt, discount)
    if effective <= 0:
        await _grant_free(callback, lang, plan, tariff_id, opt)
        return
    async with get_session() as db:
        prices = await compute_prices(effective)
        sub = await subscriptions.create_pending(
            db, telegram_id=user.id, tariff=Tariff(tariff_id), provider_name="stars",
            username=user.username, full_name=user.full_name, period_days=opt.days,
        )
        await db.commit()
        sub_id = sub.id
    await callback.message.answer_invoice(
        title=t(lang, "sub_stars_title", title=plan.title),
        description=t(lang, "sub_stars_desc", title=plan.title, days=opt.days),
        payload=f"sub:{sub_id}",
        provider_token="",  # empty for Telegram Stars (XTR)
        currency="XTR",
        prices=[LabeledPrice(label=plan.title, amount=prices.stars)],
    )
    await callback.answer()


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    lang = lang_of(message)
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("sub:"):
        return
    sub_id = int(payload.split(":", 1)[1])
    async with get_session() as db:
        sub = await subscriptions.get_subscription(db, sub_id)
        if sub is None:
            return
        await subscriptions.activate(db, sub)
        await db.commit()
        title = get_plan(sub.tariff).title
        text = await _success_text(db, message.from_user.id, lang, title)
    await message.answer(text, reply_markup=keyboards.main_menu(lang))


# --- Crypto Pay ------------------------------------------------------------


@router.callback_query(F.data.startswith("sub:pay:") & F.data.endswith(":crypto"))
async def on_pay_crypto(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    _, _, tariff_id, dur_code, _ = callback.data.split(":")
    plan = get_plan(tariff_id)
    if not plan.available:
        # see on_pay_stars — callback_data can be forged directly, bypassing
        # on_buy's availability gate.
        await callback.answer(t(lang, "sub_unavailable"), show_alert=True)
        return
    opt = get_duration(dur_code)
    user = callback.from_user
    async with get_session() as db:
        discount = await referrals.get_discount_percent(db, user.id)
    effective = effective_profit_uah(plan.profit_uah, opt, discount)
    if effective <= 0:
        await _grant_free(callback, lang, plan, tariff_id, opt)
        return
    provider = _crypto()
    if provider is None:
        await callback.answer(t(lang, "sub_crypto_unavailable"), show_alert=True)
        return
    async with get_session() as db:
        prices = await compute_prices(effective)
        sub = await subscriptions.create_pending(
            db, telegram_id=user.id, tariff=Tariff(tariff_id), provider_name="crypto_pay",
            username=user.username, full_name=user.full_name, period_days=opt.days,
        )
        await db.commit()
        sub_id = sub.id
    try:
        invoice = await provider.create_invoice(
            amount=prices.usdt_invoice, description=t(lang, "sub_stars_title", title=plan.title), payload=f"sub:{sub_id}"
        )
    except Exception:
        logger.exception("crypto invoice failed")
        await callback.answer(t(lang, "sub_invoice_error"), show_alert=True)
        return
    async with get_session() as db:
        sub = await subscriptions.get_subscription(db, sub_id)
        sub.external_invoice_id = invoice.invoice_id
        await db.commit()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "sub_pay_btn"), url=invoice.pay_url)],
            [InlineKeyboardButton(text=t(lang, "sub_check_btn"), callback_data=f"sub:check:{sub_id}")],
        ]
    )
    await _edit(callback.message, t(lang, "sub_crypto_prompt", title=plan.title, amount=prices.usdt_invoice), kb=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("sub:check:"))
async def on_check_crypto(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    provider = _crypto()
    sub_id = int(callback.data.rsplit(":", 1)[1])
    async with get_session() as db:
        sub = await subscriptions.get_subscription(db, sub_id)
        invoice_id = sub.external_invoice_id if sub else None
    if provider is None or sub is None or invoice_id is None:
        await callback.answer(t(lang, "sub_no_invoice"), show_alert=True)
        return
    try:
        paid = await provider.is_paid(invoice_id)
    except Exception:
        logger.exception("crypto status check failed")
        await callback.answer(t(lang, "sub_check_error"), show_alert=True)
        return
    if not paid:
        await callback.answer(t(lang, "sub_not_paid"), show_alert=True)
        return
    async with get_session() as db:
        sub = await subscriptions.get_subscription(db, sub_id)
        await subscriptions.activate(db, sub)
        await db.commit()
        title = get_plan(sub.tariff).title
        needs_hint = not (await storage.get_user_status(db, callback.from_user.id)).sessions
    await _edit(callback.message, t(lang, "sub_success", title=title))
    # The card and Stars paths send one message; this one already replaced the
    # invoice above, so the keyboard has to ride on a second message either
    # way — with the nudge only when there is nothing connected yet.
    await callback.message.answer(
        t(lang, "sub_connect_hint") if needs_hint else t(lang, "sub_ready"),
        reply_markup=keyboards.main_menu(lang),
    )
    await callback.answer()


# --- WayForPay (bank card, UAH) --------------------------------------------


@router.callback_query(F.data.startswith("sub:pay:") & F.data.endswith(":card"))
async def on_pay_card(callback: CallbackQuery) -> None:
    lang = lang_of(callback)
    _, _, tariff_id, dur_code, _ = callback.data.split(":")
    plan = get_plan(tariff_id)
    if not plan.available:
        # see on_pay_stars — callback_data can be forged directly, bypassing
        # on_buy's availability gate.
        await callback.answer(t(lang, "sub_unavailable"), show_alert=True)
        return
    opt = get_duration(dur_code)
    user = callback.from_user
    async with get_session() as db:
        discount = await referrals.get_discount_percent(db, user.id)
    effective = effective_profit_uah(plan.profit_uah, opt, discount)
    if effective <= 0:
        await _grant_free(callback, lang, plan, tariff_id, opt)
        return

    provider = build_wayforpay()
    base = connect_server.base_url
    if provider is None or not base:
        # No gateway configured, or no public origin to host the payment page
        # on — either way there is nothing to send the user to.
        await callback.answer(t(lang, "sub_card_unavailable"), show_alert=True)
        return

    prices = await compute_prices(effective)
    # Auto-renewal only for periods WayForPay can express. Anything else is
    # sold as a one-off rather than silently renewed on the wrong schedule.
    auto_renew = opt.days in REGULAR_MODE_BY_DAYS

    async with get_session() as db:
        sub = await subscriptions.create_pending(
            db, telegram_id=user.id, tariff=Tariff(tariff_id), provider_name="wayforpay",
            username=user.username, full_name=user.full_name, period_days=opt.days,
            auto_renew=auto_renew, amount_uah=prices.uah_invoice,
        )
        # The reference embeds the id, so it can only be built after the flush
        # inside create_pending has assigned one.
        sub.order_reference = subscriptions.build_order_reference(sub.id)
        await db.commit()
        order_reference = sub.order_reference

    # A plain link, not a Mini App button: the payment page opens in the
    # system browser, where Apple Pay, Google Pay and 3-D Secure all work.
    # An embedded WebView would have put those at risk for no gain.
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(lang, "sub_pay_card_btn"), url=f"{base.rstrip('/')}/pay/{order_reference}"
                )
            ]
        ]
    )
    prompt_key = "sub_card_prompt" if auto_renew else "sub_card_prompt_once"
    await _edit(
        callback.message,
        t(lang, prompt_key, title=plan.title, amount=prices.uah_invoice, days=opt.days),
        kb=kb,
    )
    await callback.answer()
