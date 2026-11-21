# Liquidador TSE

Herramienta de la Oficina de Negocios Verdes y Sostenibles (ONVS) del
Ministerio de Ambiente y Desarrollo Sostenible de Colombia para las
**Transferencias del Sector Eléctrico** (art. 45 de la Ley 99 de 1993,
reglamentado por el Decreto 800 de 2026).

Hace dos cosas:

1. **Establece porcentajes.** A partir de cada delimitación del IGAC
   (hectáreas S1 y S2 por autoridad ambiental), calcula la participación de
   cada CAR o de Parques Nacionales en cada proyecto hidroeléctrico.
2. **Liquida el mes.** Con la generación de XM y la tarifa de venta en bloque
   (TVB) de la CREG, calcula el valor a transferir a cada autoridad:
   `generación × TVB × 3 % × participación`.

El código llega por pull request desde la rama `propuesta/liquidador-inicial`.
