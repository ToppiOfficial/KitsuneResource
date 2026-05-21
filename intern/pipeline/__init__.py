from .model_pipeline import ValveModelPipeline
from .texture_pipeline import ValveTexturePipeline

PIPELINE_REGISTRY = {
    "ValveModel":   ValveModelPipeline,
    "ValveTexture": ValveTexturePipeline,
}
