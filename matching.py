"""Сбор за подбор для заявок с сайта с согласованной ставкой исполнителя."""

TIERS = {"economy": ("Эконом", 99), "comfort": ("Комфорт", 199), "professional": ("Профессиональный", 299)}
DESCRIPTIONS = {
    "economy": "Проверенный исполнитель для простых задач по вашей инструкции.",
    "comfort": "Исполнитель с опытом, дополнительные действия по договорённости и отчёт.",
    "professional": "Исполнитель с подтверждённым опытом для сложной задачи или особого ухода.",
}


async def fee(conn, user_id: int, tier: str, excluding: int = 0) -> int:
    if tier == "economy":
        # Связанные веб-профили и Telegram учитываются как один клиент.
        async with conn.execute(
            """SELECT 1 FROM orders o JOIN users account ON account.id=o.user_id
               JOIN users client ON client.id=?
               WHERE COALESCE(account.verified_tg_id, account.tg_id)=COALESCE(client.verified_tg_id, client.tg_id)
                 AND o.id<>? AND (o.match_tier='economy' OR o.match_tier IS NULL)
                 AND (o.status IN ('confirmed','paid','assigned','in_progress','completed') OR EXISTS (
                     SELECT 1 FROM order_statuses_log h WHERE h.order_id=o.id AND h.to_status='confirmed'
                 )) LIMIT 1""", (user_id, excluding)
        ) as rows:
            if await rows.fetchone() is None:
                return 0
    return TIERS[tier][1]


def breakdown(tier: str, executor_quote: int, fee_amount: int) -> list:
    return [("Работа исполнителя", executor_quote), (f"Подбор · {TIERS[tier][0]}", fee_amount)]
