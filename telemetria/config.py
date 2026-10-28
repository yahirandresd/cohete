"""Parámetros de la estación terrestre. Ajustar aquí, no en el código."""

# Red (debe coincidir con el firmware del ESP32)
UDP_PUERTO = 8888
HTTP_PUERTO = 8080
ESP32_IP = "192.168.4.1"          # IP del ESP32 en modo AP
URL_LOG_A_BORDO = f"http://{ESP32_IP}/log.csv"   # solo con firmware v2

# Física
G = 9.78                           # gravedad local en Manizales [m/s^2]
RANGO_ACC_G = 16.0                 # rango de la IMU WitMotion (+/- 16 g)

# Convención de ángulos de la IMU: R = Rz(yaw)·Ry(pitch)·Rx(SIGNO_ROLL·roll).
# Medido con los CSV de mediciones/: con roll invertido el vector gravedad en reposo
# queda a <1° de la vertical (con el signo normal, a >7°).
SIGNO_ROLL = -1

# Eje del cuerpo que apunta a la nariz del cohete ('x', 'y' o 'z').
# Se usa para calcular la inclinación respecto a la vertical.
EJE_COHETE = "z"

# Detección de reposo (para ZUPT y calibración)
REPOSO_TOL_G = 0.06                # | |a| - 1 g | máximo
REPOSO_STD_G = 0.03                # desviación estándar máxima de |a| en la ventana
REPOSO_VENTANA_S = 1.0             # duración de la ventana de reposo [s]

# Validez de la estimación de velocidad/posición
COHERENCIA_MAX = 5.0               # ° entre gravedad medida y vertical según los ángulos
INTEGRACION_MAX_S = 8.0            # s seguidos sin reposo antes de marcar la estimación como no fiable

# Fases de vuelo
EMPUJE_G = 2.5                     # |a| por encima => empuje / lanzamiento
CAIDA_LIBRE_G = 0.35               # |a| por debajo => vuelo balístico (lectura ~0 g)
TIERRA_REPOSO_S = 1.5              # tiempo quieto tras el vuelo para declarar EN TIERRA

# Enlace
HUECO_MAX_S = 1.0                  # hueco mayor a esto corta la integración
ENLACE_PERDIDO_S = 1.0             # sin paquetes durante esto => ENLACE PERDIDO
TASA_ESPERADA_HZ = 5.0             # firmware actual: 1 paquete cada 200 ms
