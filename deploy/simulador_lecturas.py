"""Simulador del ESP32 para la demo: envía lecturas en vivo a /api/lecturas/.

Cada lectura entra por el mismo punto que usa el sistema real, se guarda en Supabase y el servidor la empuja
por WebSocket a las páginas abiertas (panel de administrador), sin recargar. Los valores siguen la escala de
la maqueta: presión ~2.4-3.4, nivel del tanque 8-30 cm, TDS 120-200 ppm, flujo 0.03-0.30 L/min.

Primero ejecuta deploy\\preparar_demo.ps1 (datos frescos para la IA) y después este simulador.

Uso (desde la carpeta Backend/SmartDrop):
    .venv\\Scripts\\python.exe deploy\\simulador_lecturas.py            # cada 2 s hasta Ctrl+C
    .venv\\Scripts\\python.exe deploy\\simulador_lecturas.py --una-vez  # una ronda, para probar
"""
import argparse
import random
import time
from datetime import datetime, timezone

import requests

URL_POR_DEFECTO = 'https://smartdrop-ugb.duckdns.org/api/lecturas/'
# id_sensor en Supabase -> tipo (los 4 sensores de la maqueta)
SENSORES = {1: 'presion', 2: 'nivel', 3: 'calidad', 4: 'flujo'}


class Maqueta:
    """Estado que evoluciona poco a poco, para que los valores se vean como un sensor real y no como ruido."""

    def __init__(self):
        self.presion = 3.0
        self.nivel = 24.0
        self.calidad = 150.0
        self.flujo = 0.10
        self.bomba = False

    def avanzar(self):
        self.flujo = min(max(self.flujo + random.gauss(0, 0.02), 0.03), 0.30)
        # El tanque baja con el consumo; la bomba lo rellena al llegar a 10 cm y para en 28 cm.
        if self.nivel <= 10:
            self.bomba = True
        elif self.nivel >= 28:
            self.bomba = False
        self.nivel += (0.6 if self.bomba else 0) - self.flujo * 0.8 + random.gauss(0, 0.05)
        self.nivel = min(max(self.nivel, 8.0), 30.0)
        self.presion = min(max(self.presion + random.gauss(0, 0.04) + (0.03 if self.bomba else 0), 2.4), 3.4)
        self.calidad = min(max(self.calidad + random.gauss(0, 1.5), 120.0), 200.0)
        return {
            'presion': round(self.presion, 2),
            'nivel': round(self.nivel, 1),
            'calidad': round(self.calidad, 1),
            'flujo': round(self.flujo, 3),
        }


def enviar_ronda(sesion, url, vivienda, maqueta):
    valores = maqueta.avanzar()
    ahora = datetime.now(timezone.utc).isoformat(timespec='seconds')
    resultados = []
    for id_sensor, tipo in SENSORES.items():
        cuerpo = {'id_sensor': id_sensor, 'id_vivienda': vivienda, 'fecha_registro': ahora, 'valor': valores[tipo]}
        try:
            respuesta = sesion.post(url, json=cuerpo, timeout=8)
            estado = 'ok' if respuesta.status_code == 201 else f'HTTP {respuesta.status_code}: {respuesta.text[:80]}'
        except requests.RequestException as error:
            estado = f'sin conexión ({error.__class__.__name__})'
        resultados.append(f'{tipo}={valores[tipo]} [{estado}]')
    print(f"{datetime.now():%H:%M:%S}  " + '  '.join(resultados), flush=True)


def main():
    parser = argparse.ArgumentParser(description='Simula el ESP32 de SmartDrop enviando lecturas en vivo.')
    parser.add_argument('--url', default=URL_POR_DEFECTO)
    parser.add_argument('--vivienda', type=int, default=9,
                        help='id de la vivienda (por defecto 9 = NIC-0009, sin escenario de fuga ni de bomba).')
    parser.add_argument('--intervalo', type=float, default=2.0, help='Segundos entre rondas (por defecto 2).')
    parser.add_argument('--minutos', type=float, default=0, help='Detenerse tras N minutos (0 = hasta Ctrl+C).')
    parser.add_argument('--una-vez', action='store_true', help='Enviar una sola ronda y salir.')
    args = parser.parse_args()

    print(f'Enviando lecturas de la vivienda {args.vivienda} a {args.url}  (Ctrl+C para detener)')
    sesion = requests.Session()
    maqueta = Maqueta()
    fin = time.monotonic() + args.minutos * 60 if args.minutos else None
    try:
        while True:
            enviar_ronda(sesion, args.url, args.vivienda, maqueta)
            if args.una_vez or (fin and time.monotonic() >= fin):
                break
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        print('\nSimulador detenido.')


if __name__ == '__main__':
    main()
