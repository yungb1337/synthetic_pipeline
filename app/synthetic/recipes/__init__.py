"""Synthetic Data Generation Recipes Package."""

from app.synthetic.recipes.contradiction import ContradictionRecipe
from app.synthetic.recipes.dom_direct import DOMDirectRecipe
from app.synthetic.recipes.dpo_perturbation import DPOPerturbationRecipe
from app.synthetic.recipes.multi_hop import MultiHopRecipe
from app.synthetic.recipes.protocol import SynthesisRecipe
from app.synthetic.recipes.registry import RecipeRegistry
from app.synthetic.recipes.table_reasoning import TableReasoningRecipe

# Register standard recipes
RecipeRegistry.register(MultiHopRecipe())
RecipeRegistry.register(DPOPerturbationRecipe())
RecipeRegistry.register(TableReasoningRecipe())
RecipeRegistry.register(DOMDirectRecipe())
RecipeRegistry.register(ContradictionRecipe())

__all__ = [
    "SynthesisRecipe",
    "RecipeRegistry",
    "MultiHopRecipe",
    "DPOPerturbationRecipe",
    "TableReasoningRecipe",
    "DOMDirectRecipe",
    "ContradictionRecipe",
]
