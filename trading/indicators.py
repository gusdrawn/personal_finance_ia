INDICATORS = {
    "quant_score": {"label": "QuantPro", "short": "Score cuantitativo 0–100", "definition": "Combina valor, calidad, crecimiento, momentum y riesgo con pesos por clase de activo. Datos faltantes reducen cobertura; no se convierten en ceros.", "source": "Cálculo reproducible"},
    "fair_value": {"label": "Valor justo", "short": "Estimación fundamental", "definition": "Valor estimado a partir de los datos disponibles del proveedor. Es un rango de investigación, no una promesa de precio.", "source": "Proveedor/configuración"},
    "consensus": {"label": "Consenso WS", "short": "Consenso Wall Street", "definition": "Distribución de recomendaciones y precio objetivo reportados por analistas agregados.", "source": "Proveedor de analistas"},
    "divergence": {"label": "Divergencia", "short": "QuantPro frente a analistas", "definition": "Señala cuando el score cuantitativo y el upside/consenso apuntan en direcciones distintas.", "source": "Cálculo reproducible"},
    "rsi14": {"label": "RSI 14", "short": "Fuerza relativa", "definition": "Oscilador de 0 a 100 que compara la magnitud de avances y retrocesos de las últimas 14 sesiones.", "source": "Precios diarios"},
    "beta": {"label": "Beta", "short": "Sensibilidad al mercado", "definition": "Sensibilidad histórica del activo frente a su benchmark; 1 suele moverse como el mercado.", "source": "Precios diarios"},
    "volatility_30d": {"label": "Volatilidad 30D", "short": "Riesgo realizado", "definition": "Desviación anualizada de los retornos diarios de las últimas 30 sesiones.", "source": "Precios diarios"},
    "broker_status": {"label": "Quantfury", "short": "Estado personal", "definition": "Disponibilidad que tú confirmas para tu cuenta. UNKNOWN no significa que el activo no exista.", "source": "Confirmación del usuario"},
}


def indicator(key: str) -> dict:
    return INDICATORS.get(key, {"label": key, "definition": "Indicador sin descripción registrada.", "source": "Sin fuente"})
