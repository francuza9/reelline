"""The bot's /costs and /analytics."""

from __future__ import annotations

from reelline.config import conf
from reelline.notify.base import NotifyError, buttons, split_text
from reelline.pipeline import analytics, costs
from reelline.pipeline.approve_bot.state import BotState
from reelline.review.base import Tap


class ReportsMixin(BotState):
    def on_costs_command(self, args: list[str]) -> None:
        if not args:
            b = conf().costs.buttons
            self.say(
                "API costs: which month?",
                buttons([[(b["this"], "c:m:this"), (b["last"], "c:m:last"), (b["pick"], "c:pick")]]),
            )
            return
        try:
            month = costs.parse_month(args[0])
        except costs.CostsError as exc:
            self.say(f"{exc}. /costs or /costs YYYY-MM")
            return
        self.send_costs(month)

    def on_costs_tap(self, query: Tap, data: str) -> None:
        parts = data.split(":")
        try:
            if parts[1] == "pick":
                months = costs.recent_months(self.now(), conf().costs.months_back)
                rows = [[(m.short, f"c:m:{m.key}") for m in months[i : i + 3]] for i in range(0, len(months), 3)]
                self.tg.edit(query.ref, "API costs: pick a month", buttons(rows))
                self.answer(query, "")
                return
            if parts[1] == "m":
                now_month = costs.month_of(self.now())
                month = {"this": now_month, "last": now_month.prev()}.get(parts[2]) or costs.parse_month(parts[2])
                self.answer(query, month.label)
                self.send_costs(month)
                return
            if parts[1] == "csv":
                month = costs.parse_month(parts[2])
                self.answer(query, "CSV")
                rows, world = costs.month_costs(self.conn, month)
                self.tg.send_file(
                    f"{conf().channel.name}_costs_{month.key}.csv",
                    costs.csv_text(rows, world).encode("utf-8"),
                    kind="document",
                    mime="text/csv",
                    caption=f"Cost rows, {month.label} ({len(rows)} rows, Tbilisi month)",
                )
                return
        except (IndexError, costs.CostsError):
            pass
        except NotifyError as exc:
            self.log(f"/costs send failed: {exc}")
            self.say(f"⚠️ /costs: {exc}")
            return
        self.answer(query, "Unknown button")

    def send_costs(self, month: costs.Month) -> None:
        """The month's report, split only when over Telegram's limit; the CSV button on
        the last part."""
        chunks = split_text(costs.format_report(costs.report(self.conn, month, self.now())), self.tg.text_limit)
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            self.say(chunk, buttons([[(conf().costs.buttons["csv"], f"c:csv:{month.key}")]]) if last else None)

    # -- /analytics --

    def on_analytics_command(self, args: list[str]) -> None:
        if not args:
            b = conf().steps.analytics.buttons
            self.say(
                "Analytics: which period?", buttons([[(b[k], f"a:p:{k}") for k in conf().steps.analytics.periods]])
            )
            return
        try:
            reel_id = int(args[0].lstrip("#"))
        except ValueError:
            self.say(f"Not a reel number: {args[0]}. /analytics or /analytics <reel_id>")
            return
        try:
            data = analytics.load(self.conn, self.now())
            self.send_chunks(
                analytics.format_reel(analytics.build_reel(data, reel_id, analytics.reel_cost(self.conn, reel_id)))
            )
        except analytics.AnalyticsError as exc:
            self.say(f"{exc}.")

    def on_analytics_tap(self, query: Tap, data: str) -> None:
        parts = data.split(":")
        if len(parts) == 3 and parts[1] == "p" and parts[2] in conf().steps.analytics.periods:
            self.answer(query, conf().steps.analytics.periods[parts[2]])
            try:
                self.send_chunks(
                    analytics.format_overview(analytics.build_overview(analytics.load(self.conn, self.now()), parts[2]))
                )
            except NotifyError as exc:
                self.log(f"/analytics send failed: {exc}")
            return
        self.answer(query, "Unknown button")

    def send_chunks(self, text: str) -> None:
        """One message, split only when over Telegram's limit."""
        for chunk in split_text(text, self.tg.text_limit):
            self.say(chunk)
