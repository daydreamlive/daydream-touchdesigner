def onHTTPRequest(webServerDAT, request, response):
	print(f"Daydream WebServer: {webServerDAT.name} received request: {request.get('uri', '/')}")
	ext = getattr(webServerDAT.parent().ext, 'Daydream', None)
	if not ext:
		ext = getattr(webServerDAT.parent().ext, 'DaydreamExt', None)
	
	if ext:
		name = webServerDAT.name.lower()
		if 'auth' in name:
			server_type = 'auth'
		elif 'sdp' in name:
			server_type = 'sdp'
		else:
			server_type = 'frame'
		print(f"Daydream WebServer: routing to {server_type}")
		ext.OnHTTPRequest(request, response, server_type)
	else:
		print("Daydream WebServer: Extension not found!")
		response['statusCode'] = 500
		response['data'] = b'Extension not found'
	
	return response

def onWebSocketOpen(webServerDAT, client, uri):
	ext = getattr(webServerDAT.parent().ext, 'Daydream', None)
	if not ext:
		ext = getattr(webServerDAT.parent().ext, 'DaydreamExt', None)
	if ext and hasattr(ext, 'OnWebSocketOpen'):
		ext.OnWebSocketOpen(client, uri)
	return

def onWebSocketClose(webServerDAT, client):
	ext = getattr(webServerDAT.parent().ext, 'Daydream', None)
	if not ext:
		ext = getattr(webServerDAT.parent().ext, 'DaydreamExt', None)
	if ext and hasattr(ext, 'OnWebSocketClose'):
		ext.OnWebSocketClose(client)
	return

def onWebSocketReceiveText(webServerDAT, client, data):
	ext = getattr(webServerDAT.parent().ext, 'Daydream', None)
	if not ext:
		ext = getattr(webServerDAT.parent().ext, 'DaydreamExt', None)
	if ext and hasattr(ext, 'OnWebSocketReceiveText'):
		ext.OnWebSocketReceiveText(client, data)
	return

def onWebSocketReceiveBinary(webServerDAT, client, data):
	return

def onWebSocketReceivePing(webServerDAT, client, data):
	return

def onWebSocketReceivePong(webServerDAT, client, data):
	return

def onServerStart(webServerDAT):
	print(f"Daydream: Web Server started on port {webServerDAT.par.port.eval()}")
	return

def onServerStop(webServerDAT):
	print("Daydream: Web Server stopped")
	return
