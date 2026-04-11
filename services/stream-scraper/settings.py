from pydantic import BaseModel
from cat.mad_hatter.decorators import plugin

class ScraperSettings(BaseModel):
    # URL di Ollama visto dall'interno del container Docker
    # Se sei su Linux/Mac e host.docker.internal non va, usa l'IP locale del PC (es. http://192.168.1.X:11434)
    ollama_url: str = "http://host.docker.internal:11434"
    
    # Il modello Vision da usare (assicurati di aver fatto 'ollama pull llama3.2-vision')
    vision_model: str = "llama3.2-vision"
    
    # Se True, il browser gira in background.
    # Mettilo a False solo se stai eseguendo il Gatto fuori da Docker per vedere cosa fa.
    headless_mode: bool = True

    # Salva gli screenshot dei click per capire se l'IA sta mirando giusto
    save_debug_screenshots: bool = True

@plugin
def settings_model():
    return ScraperSettings