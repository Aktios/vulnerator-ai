---
name: l3-recon-pivot
description: Enumera topologia de red en capa 3 tras NAT, valida hallazgos y reconoce cuando un objetivo es inalcanzable desde tu posicion.
version: 0.1.0
author: vulnerator-ai
license: MIT
metadata:
  hermes:
    tags: [Security, Pentest, Network, Recon, L3]
    related_skills: [pentest-ooda]
    # Activacion condicional: solo aparece si la tool de ACCION del MCP de la Kali esta presente,
    # igual que pentest-ooda. Si no hay red que enumerar, este conocimiento NO ensucia el prompt.
    requires_tools: [mcp_kali_kali_run]
---

# Reconocimiento L3 y pivoting tras NAT

Conocimiento de dominio para la fase de reconocimiento de red cuando el objetivo esta en OTRA subred
que la tuya y hay NAT de por medio (p. ej. corres en un contenedor). Complementa `pentest-ooda`: no
cambia la disciplina del bucle, aporta la tecnica para enumerar capa 3 y, sobre todo, para
distinguir "sigo descubriendo terreno" de "ya se que no puedo llegar".

## When to Use

Cuando el objetivo NO esta en tu misma subred y necesitas descubrir la topologia (gateways, saltos,
subredes) o decidir si el objetivo es siquiera alcanzable desde tu posicion. Si el objetivo ya esta
en tu segmento o te lo dieron por IP, NO necesitas esto: ve directo al escaneo.

## Mapear tu posicion antes de escanear nada

Antes de barrer rangos, situate. Un par de lecturas baratas te dicen donde estas y hacia donde hay
salida:

- **Tu subred y tu gateway**: `ip route` / `/proc/net/route`, `ip addr`. El `default via <gw>` es tu
  unica salida L3; todo lo que no sea tu subred local sale por ahi.
- **Tu vecindario L2**: `ip neigh` / `arp -a`. SOLO ve tu dominio de broadcast local. Si el objetivo
  no esta aqui, ARP no te lo va a encontrar nunca (no cruza routers ni NAT).
- **Resolucion y salida a internet**: `/etc/resolv.conf` (DNS que te sirven), `curl -s ifconfig.me`
  o `cloudflare.com/cdn-cgi/trace` (tu IP publica efectiva). Util para entender por donde sales, NO
  para encontrar un objetivo en LAN ajena.

Con esto ya sabes: cuantos saltos de NAT tienes delante y si el objetivo cae dentro o fuera de tu
alcance L3.

## Entender NAT en capas (el caso del contenedor)

Si corres en un contenedor, lo normal es tener VARIAS capas de NAT entre tu y una LAN "de verdad":

```
contenedor (tu)  ->  gateway de la red del contenedor  ->  [host / VM]  ->  router real  ->  internet
```

Cada flecha que cruza un NAT es una frontera que NO puedes atravesar a nivel L2/L3 solo con escaneo:
el NAT reescribe direcciones y no reenvia tu trafico arbitrario hacia la subred de detras. Reconocer
en que capa estas es lo que te dice si un objetivo es alcanzable o no. Un `traceroute` a un destino
de internet te muestra tus primeros saltos; si el segundo salto ya no responde (`* * *`), tienes un
NAT/filtrado delante y la subred de mas alla NO esta a tu alcance directo.

## Validar hallazgos antes de construir sobre ellos

Un hallazgo de descubrimiento NO es un hecho hasta que lo confirmas con una segunda senal
independiente. Firewalls y NAT producen FALSOS POSITIVOS que arruinan el OODA si los tratas como
reales:

- `nmap -sn <rango>` que reporta "N hosts activos" puede ser el firewall respondiendo por toda la
  subred. **Confirma** con un `ping` real o un puerto que responda de verdad antes de dar un host
  por vivo. "Host up" sin paquete que lo corrobore = sospechoso.
- Un puerto "open|filtered" (UDP, o TCP tras firewall) NO es un puerto abierto confirmado.
- Si una subred entera aparece "llena de hosts" de golpe, desconfia: es el patron tipico de un
  dispositivo que ACKea todo. Verifica uno antes de escanearlos todos.

Regla: no bases el siguiente paso en un hallazgo que no has corroborado. Un falso positivo
perseguido es un ciclo OODA tirado.

## Pivoting cuando hay un intermediario

Si descubres un servicio que puede reenviar trafico por ti (un proxy HTTP tipo Squid, un host con
doble pata, un SSH accesible), esa es la via LEGITIMA de llegar mas lejos cuando el escaneo directo
no cruza. Pruebalo de forma acotada antes de asumir nada: p. ej. `curl -x http://<proxy>:<puerto>
<url-de-prueba>` para ver si el proxy te saca a otra red, o si solo repite tu misma salida. Un proxy
que te devuelve TU MISMA IP publica no te esta pivotando a ningun sitio nuevo.

## Reconocer inalcanzabilidad (criterio de PARADA)

Esta es la parte que mas importa, porque es donde el bucle se descarrila. Si tras unas pocas tacticas
DISTINTAS (mapear ruta, ARP, traceroute, un intento de pivot) confirmas que:

- el objetivo esta detras de uno o mas NAT que no puedes cruzar, y
- no tienes ningun intermediario que reenvie hacia su subred,

entonces el objetivo es **inalcanzable desde tu posicion**. Eso NO es un paso de progreso aunque cada
intento te haya dado salida nueva: has aprendido sobre un terreno que no contiene la respuesta. PARA,
consolida la topologia que SI descubriste, y DEVUELVE el control al operador explicando por que es
inalcanzable y que necesitarias (que te den la IP, otra posicion de red, un pivot valido). No sigas
barriendo subredes al azar: mas intentos no abriran una ruta que fisicamente no existe.

## Pitfalls

- **Confundir "descubri algo nuevo" con "avance hacia el objetivo".** Mapear la enesima subred de la
  infra de contenedores no te acerca a un router en LAN ajena.
- **Perseguir falsos positivos** del firewall/NAT sin confirmarlos con una segunda senal.
- **Repetir la misma tactica** esperando otro resultado (otro traceroute, otro barrido del mismo
  rango): si una via ya demostro no cruzar, repetirla no es progreso.
- **Barrer rangos privados al azar** (192.168.x, 10.x) "por si suena": sin ruta L3 hacia ellos, no
  salen de tu NAT.

## Verification

Antes de dar por buena la fase de recon L3, confirma: (a) situaste tu posicion (subred, gateway,
capas de NAT) antes de barrer; (b) todo host/puerto que das por vivo esta corroborado por una segunda
senal, no por un solo `nmap -sn`; (c) si el objetivo resulto inalcanzable, PARASTE y lo reportaste en
vez de seguir descubriendo terreno irrelevante.
