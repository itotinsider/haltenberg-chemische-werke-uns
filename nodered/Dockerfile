# Node-RED with the OPC UA client nodes baked into the image. /data is a bind mount
# (./nodered_data), so nodes installed there would vanish on a fresh checkout. Installing next to
# Node-RED itself keeps the flow self-contained in the image.
FROM nodered/node-red:4.1.4
RUN npm install --no-audit --no-fund --omit=dev node-red-contrib-opcua@0.2.355
