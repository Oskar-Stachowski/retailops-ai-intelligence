"""Assemble the complete eight-tool catalog from server-owned verified readers."""

from typing import Literal

from retailops_ai.adapters.forecast_v12_tool import NativeForecastReader, NativeForecastTool
from retailops_ai.adapters.native_anomaly_tool import NativeAnomalyReader, NativeAnomalyTool
from retailops_ai.adapters.native_inventory_tool import InventoryReader, NativeInventoryTool
from retailops_ai.adapters.native_model_status_tool import (
    NativeAnomalyCatalog,
    NativeForecastCatalog,
    NativeModelStatusTool,
)
from retailops_ai.adapters.native_operations_tool import (
    NativeOperationsReader,
    NativeOperationsTool,
)
from retailops_ai.adapters.native_stockout_tool import NativeStockoutReader, NativeStockoutTool
from retailops_ai.adapters.qualified_sales_tool import QualifiedSalesTool, SalesReader
from retailops_ai.agent.execution import ToolAdapter


def native_assistant_tools(
    *,
    environment: Literal["local", "test"],
    sales: SalesReader,
    inventory: InventoryReader,
    forecast: NativeForecastReader,
    stockout: NativeStockoutReader,
    anomaly: NativeAnomalyReader,
    operations: NativeOperationsReader,
    forecast_catalog: NativeForecastCatalog,
    anomaly_catalog: NativeAnomalyCatalog,
    knowledge: ToolAdapter,
) -> dict[str, ToolAdapter]:
    return {
        "get_sales_summary": QualifiedSalesTool(sales, environment),
        "get_inventory_status": NativeInventoryTool(inventory, environment),
        "get_demand_forecast": NativeForecastTool(forecast, environment),
        "get_stockout_risk": NativeStockoutTool(stockout, inventory, environment),
        "get_detected_anomalies": NativeAnomalyTool(anomaly, environment),
        "get_live_operations": NativeOperationsTool(operations, environment),
        "get_model_status": NativeModelStatusTool(forecast_catalog, anomaly_catalog, environment),
        "search_knowledge": knowledge,
    }
