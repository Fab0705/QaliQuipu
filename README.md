# 🚀 KAIROS - QaliQuipu

[![Python](https://img.shields.io/badge/Python-3.x-blue)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/Database-SQLite-003B57)](https://www.sqlite.org/)
[![Gemma 4](https://img.shields.io/badge/AI-Gemma_4_by_Google-orange)](#)
[![Ollama](https://img.shields.io/badge/Local_LLM-Ollama-white)](#)

**Inteligencia preventiva para la salud del Perú profundo.** 

QaliQuipu es un orquestador logístico preventivo diseñado para postas médicas en zonas rurales. Operando bajo una filosofía 100% "Offline-First", utiliza el poder de Gemma 4 para predecir quiebres de stock médico sin necesidad de conexión a internet.

🏆 **Proyecto desarrollado para la Hackathon: Build with Gemma - GDG Lima - AI Competition.**
📍 **Tracks:** IA Local e Inteligencia Edge, IA para Impacto Social, Agentes de IA y Automatización.

---

## 🏥 El Problema

La urgencia de implementar QaliQuipu responde a la crítica realidad tecnológica y logística de las zonas rurales en el Perú:
*   **Desconexión extrema:** Solo el 20,5% de hogares rurales accede a internet y apenas el 8,2% posee una computadora, haciendo inviable depender de la nube.
*   **Crisis en primera línea:** En las postas médicas de atención primaria (categoría I-1), únicamente el 15,9% cuenta con infraestructura tecnológica operativa.
*   **Sistemas Incompatibles:** Las soluciones modernas de Punto de Venta (POS) fracasan sin internet, y los sistemas offline básicos son puramente reactivos y sin capacidad de análisis.

---

## 💡 La Solución (QaliQuipu)

QaliQuipu dota a las boticas rurales de un "cerebro" logístico impulsado por IA, capaz de analizar el comportamiento de las ventas y el inventario para alertar sobre futuros quiebres de stock antes de que sucedan, de manera 100% desconectada.

### 🏗️ Arquitectura y Tecnologías

El sistema fue diseñado priorizando la simplicidad de despliegue en computadoras de bajos recursos, utilizando una arquitectura de script monolítico.

| Capa | Tecnología | Descripción |
| :--- | :--- | :--- |
| **Frontend & Orquestación** | Python (CustomTkinter) | Interfaz de escritorio moderna controlada por un script principal (`index.py`). |
| **Inteligencia Artificial** | Gemma 4 + Ollama | Motor de razonamiento ejecutado 100% local a través de una API en el puerto 11434. |
| **Base de Datos** | SQLite | Almacenamiento local embebido (`qalinode_pos.db`) para garantizar la persistencia offline. |
| **Notificaciones** | PyWhatKit | Envío de alertas automatizadas por WhatsApp al detectar conectividad. |
| **Despliegue** | PyInstaller | Empaquetado en un único ejecutable ligero ("Plug and Play") llamado `ChasquiLog`. |

---

## ✨ Características y Retos Técnicos

*   **100% Offline-First:** Se descartó por completo la arquitectura Cloud (como Supabase) para garantizar que el análisis predictivo funcione en postas médicas totalmente aisladas.
*   **Despliegue sin dependencias:** Para evitar sobrecargar equipos básicos (poca RAM, sin GPUs), se descartaron arquitecturas complejas (microservicios o VSA). El proyecto se compiló en un ejecutable directo (`ChasquiLog`), eliminando la necesidad de instalar Python o librerías en la máquina final.
*   **Insights Predictivos Estructurados:** El controlador inyecta el contexto (inventario SQLite y reportes) a Gemma 4, y este devuelve un JSON estructurado con predicciones que se renderizan instantáneamente en el Dashboard.

---

## 📂 Estructura del Repositorio

El repositorio incluye el código fuente, bases de datos locales y los binarios compilados listos para su distribución rural.

*   📄 `index.py` - Controlador principal y orquestador de UI.
*   🗄️ `qalinode_pos.db` - Base de datos SQLite de producción.
*   📦 `/build/ChasquiLog` - Directorio con los archivos generados por PyInstaller para la ejecución standalone.
*   📄 `PyWhatKit_DB.txt` - Archivo de caché/registro del módulo de alertas de WhatsApp.

---

## 👨‍💻 Autores

**Equipo KAIROS**
*   **[Alberto Sarapura](https://www.linkedin.com/in/alberto-sarapura/)**
*   **[Erick Agreda](https://www.linkedin.com/in/erick-agreda-a39370225/)**
*   **[Fabian Cristobal](https://www.linkedin.com/in/fabiancristobal/)** (Creator)
