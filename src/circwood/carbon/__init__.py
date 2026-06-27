"""Carbon overlay: convert wood-volume flows to embodied-carbon flows.

Species-accurate model driven by editable CSV config (see species_model).
"""

from circwood.carbon.carbon import add_carbon, carbon_sankey, load_model, species_mix

__all__ = ["add_carbon", "carbon_sankey", "species_mix", "load_model"]
