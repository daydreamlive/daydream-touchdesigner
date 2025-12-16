# me - this DAT
# par - the Par object that has changed
# val - the current value
# prev - the previous value
# 
# Make sure the corresponding toggle is enabled in the Parameter Execute DAT.

def onValueChange(par, prev):
    ext = parent().ext.Daydream
    if ext:
        ext.OnParameterChange(par)
    return

def onValuesChanged(changes):
	for c in changes:
		par = c.par
		prev = c.prev
	return

def onPulse(par):
	ext = parent().ext.Daydream
	if not ext:
		return
	if par.name == "Login":
		ext.Login()
	elif par.name == "Resetparameters":
		ext.ResetParameters()
	return

def onExpressionChange(par, val, prev):
	return

def onExportChange(par, val, prev):
	return

def onEnableChange(par, val, prev):
	return

def onModeChange(par, val, prev):
	return
	
