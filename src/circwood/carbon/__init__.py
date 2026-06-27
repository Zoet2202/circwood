"""Carbon overlay: convert wood-volume flows to embodied-carbon flows."""

from circwood.carbon.carbon import (
    CARBON_DENSITY,
    add_carbon,
    carbon_sankey,
    density_for_flow,
)

__all__ = ["CARBON_DENSITY", "add_carbon", "carbon_sankey", "density_for_flow"]
