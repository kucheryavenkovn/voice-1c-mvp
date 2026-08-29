# START_MODULE_CONTRACT
#   PURPOSE: Execution gate: write-эффекты только для подтверждённого PendingAction.
#   SCOPE: Снимок корзины в контракт 1С, guard готовности frame к исполнению.
#   DEPENDS: scenarios.models
#   LINKS: M-SCENARIO-MANAGER, M-1C-ADAPTER, DF-PART-ORDER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   build_repair_payload - снимок корзины (identity 1С) для create_repair_order
#   execution_guard - None если исполнение разрешено, иначе причина запрета
# END_MODULE_MAP

"""Execution gate: write-эффекты только для подтверждённого PendingAction.

Правило безопасности: executor вызывается ТОЛЬКО когда ScenarioManager проверил,
что PendingAction соответствует текущей версии frame и все required-поля
заполнены/resolved. Любое изменение данных после показа подтверждения делает
PendingAction недействительным (stale) — документы не создаются.
"""

from __future__ import annotations

from collections.abc import Callable

from .models import ScenarioFrame

# (action_type, frame, payload) -> dict результат для ответа
ExecutorFn = Callable[[str, ScenarioFrame, dict], dict]


def build_repair_payload(frame: ScenarioFrame) -> dict:
    """Снимок корзины в контракт onec.create_repair_order (по значению, не по ссылке).
    identity = ref/код 1С; имя не подменяет идентичность."""
    vehicle = frame.fields.get("vehicle")
    items = []
    for item in frame.collections.get("items", []):
        nom = item.fields.get("nomenclature")
        qty = item.fields.get("quantity")
        items.append(
            {
                "name": nom.entity.name if nom and nom.entity else "",
                "code": (nom.entity.ref or nom.entity.code) if nom and nom.entity else None,
                "qty": int(qty.value or 1) if qty else 1,
            }
        )
    return {
        "vehicle_name": vehicle.entity.name if vehicle and vehicle.entity else None,
        "vehicle_ref": (vehicle.entity.ref or vehicle.entity.code)
        if vehicle and vehicle.entity
        else None,
        "items": items,
    }


def execution_guard(frame: ScenarioFrame) -> str | None:
    """None если исполнение разрешено, иначе причина запрета."""
    if frame.status != "active":
        return f"frame не активен ({frame.status})"
    unresolved = frame.unresolved_required()
    if unresolved:
        return f"не заполнено: {', '.join(unresolved)}"
    if frame.pending_action is None:
        return "нет подтверждённого действия"
    return None


# GRACE: стабильный публичный экспорт (для точной проверки поверхности)
__all__ = [
    "build_repair_payload",
    "execution_guard",
]
