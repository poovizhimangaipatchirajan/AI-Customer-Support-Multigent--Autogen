
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("NovaCart Demo Tools")

DEMO_ORDERS = {
    "DEMO-1001": {
        "product": "Wireless Mouse",
        "status": "SHIPPED",
        "tracking": "DEMO-TRACK-1",
    },
    "DEMO-1002": {
        "product": "Keyboard",
        "status": "PROCESSING",
        "tracking": None,
    },
}


@mcp.tool()
def get_product_information(product_id: str) -> dict:
    """Retrieve public information from the fictional demo catalog."""
    products = {
        "MOUSE-01": "Wireless Mouse",
        "KEYBOARD-01": "Keyboard",
    }
    return {
        "found": product_id in products,
        "product": products.get(product_id),
        "demo_data": True,
    }


@mcp.tool()
def check_refund_review_threshold(amount_inr: float) -> dict:
    """Provide informational threshold guidance; never approve or pay."""
    if not 0 <= amount_inr <= 1_000_000:
        return {"error": "Invalid amount"}

    return {
        "requires_human_review": amount_inr >= 500,
        "decision": "NOT_MADE",
        "execution_status": "NOT_CONFIGURED",
        "demo_data": True,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
