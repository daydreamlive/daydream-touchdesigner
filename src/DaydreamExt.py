import json
import urllib.request
import urllib.error
import http.client
import ssl
import os
import secrets
import socket
import webbrowser
import base64
from concurrent.futures import ThreadPoolExecutor

VERSION = "0.1.3"

PUBLIC_CONTRACT = {
    'extension_name': 'Daydream',
    'lifecycle_methods': ['Login', 'Start', 'Stop', 'ResetParameters'],
    'state_properties': ['state', 'Active', 'IsLoggedIn', 'ApiToken', 'stream_id', 'whip_url', 'whep_url'],
    'states': ['IDLE', 'CREATING', 'STREAMING', 'ERROR'],
    'required_operators': ['web_server', 'web_server_sdp', 'web_server_auth', 'web_render', 'stream_source', 'frame_timer'],
    'listener_api': ['register_listener', 'unregister_listener'],
    'events': [
        'initialized',
        'login_started', 'login_success', 'login_failed',
        'stream_create_started', 'stream_created', 'stream_create_failed',
        'streaming_started', 'streaming_stopped',
        'params_update_scheduled', 'params_update_sent', 'params_update_result',
        'state_changed', 'error',
    ],
}


class IPv4HTTPConnection(http.client.HTTPConnection):
    def connect(self):
        for res in socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            try:
                self.sock = socket.socket(af, socktype, proto)
                self.sock.settimeout(self.timeout)
                self.sock.connect(sa)
                break
            except OSError:
                if self.sock:
                    self.sock.close()
                    self.sock = None
        if self.sock is None:
            raise OSError(f"Failed to connect to {self.host}:{self.port} via IPv4")
        if self._tunnel_host:
            self._tunnel()


class IPv4HTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        for res in socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            try:
                sock = socket.socket(af, socktype, proto)
                sock.settimeout(self.timeout)
                sock.connect(sa)
                break
            except OSError:
                if sock:
                    sock.close()
                sock = None
        if sock is None:
            raise OSError(f"Failed to connect to {self.host}:{self.port} via IPv4")
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            server_hostname = self._tunnel_host
        else:
            server_hostname = self.host
        self.sock = self._context.wrap_socket(sock, server_hostname=server_hostname)


class IPv4HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(IPv4HTTPConnection, req)


class IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(IPv4HTTPSConnection, req, context=self._context)


CONTROLNET_SUPPORT = {
    "stabilityai/sdxl-turbo": {
        "depth": ("xinsir/controlnet-depth-sdxl-1.0", "depth_tensorrt"),
        "canny": ("xinsir/controlnet-canny-sdxl-1.0", "canny"),
        "tile": ("xinsir/controlnet-tile-sdxl-1.0", "feedback"),
    },
    "stabilityai/sd-turbo": {
        "depth": ("thibaud/controlnet-sd21-depth-diffusers", "depth_tensorrt"),
        "canny": ("thibaud/controlnet-sd21-canny-diffusers", "canny"),
        "hed": ("thibaud/controlnet-sd21-hed-diffusers", "hed"),
        "openpose": ("thibaud/controlnet-sd21-openpose-diffusers", "openpose"),
        "color": ("thibaud/controlnet-sd21-color-diffusers", "passthrough"),
    },
    "Lykon/dreamshaper-8": {
        "depth": ("lllyasviel/control_v11f1p_sd15_depth", "depth_tensorrt"),
        "canny": ("lllyasviel/control_v11p_sd15_canny", "canny"),
        "tile": ("lllyasviel/control_v11f1e_sd15_tile", "feedback"),
    },
    "prompthero/openjourney-v4": {
        "depth": ("lllyasviel/control_v11f1p_sd15_depth", "depth_tensorrt"),
        "canny": ("lllyasviel/control_v11p_sd15_canny", "canny"),
        "tile": ("lllyasviel/control_v11f1e_sd15_tile", "feedback"),
    },
}

CONTROLNET_PARAM_MAP = {
    "depth": "Depth",
    "canny": "Canny",
    "tile": "Tile",
    "hed": "Hed",
    "openpose": "Openpose",
    "color": "Color",
}

CN_PARAMS_SET = {'Depth', 'Canny', 'Tile', 'Hed', 'Openpose', 'Color'}
IP_PARAMS_SET = {'Ipadapter', 'Ipadapterscale', 'Styleimage', 'Ipadaptertype'}

IP_ADAPTER_SUPPORT = {
    "stabilityai/sdxl-turbo": {"regular", "faceid"},
    "stabilityai/sd-turbo": set(),
    "Lykon/dreamshaper-8": {"regular"},
    "prompthero/openjourney-v4": {"regular"},
}

ALL_WATCHED_PARAMS = [
    "Login", "Resetparameters", "Active", "Model", "Prompt", "Negprompt", "Seed",
    "Guidance", "Delta", "Steps", "Stepschedule*",
    "Noise", "Width", "Height",
    "Depth", "Canny", "Tile", "Hed", "Openpose", "Color",
    "Ipadapter", "Ipadapterscale", "Styleimage", "Ipadaptertype",
]

PARAM_DEFAULTS = {
    'Prompt': 'strawberry',
    'Negprompt': 'blurry, low quality, flat, 2d',
    'Seed': 42,
    'Noise': True,
    'Guidance': 1.0,
    'Delta': 0.7,
    'Steps': 50,
    'Width': '512',
    'Height': '512',
    'Depth': 0.45,
    'Canny': 0.0,
    'Tile': 0.21,
    'Hed': 0.0,
    'Openpose': 0.0,
    'Color': 0.0,
    'Ipadapter': True,
    'Ipadapterscale': 0.5,
    'Ipadaptertype': 'regular',
    'Styleimage': '',
    'Model': 'stabilityai/sdxl-turbo',
    'Active': False,
}


class DaydreamAPI:
    BASE_URL = "https://api.daydream.live/v1"

    def __init__(self, token=None):
        self.token = token
        self.ssl_ctx = ssl._create_unverified_context()
        self._opener = urllib.request.build_opener(
            IPv4HTTPHandler(),
            IPv4HTTPSHandler(context=self.ssl_ctx)
        )

    def set_token(self, token):
        self.token = token

    def _get_headers(self):
        if not self.token:
            raise ValueError("API Token is not set")
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "x-client-source": "touchdesigner",
        }

    def create_stream(self, model_id="stabilityai/sdxl-turbo", **params):
        url = f"{self.BASE_URL}/streams"
        payload = {
            "pipeline": "streamdiffusion",
            "params": {"model_id": model_id, **params}
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=self._get_headers(), method="POST")
        try:
            with self._opener.open(req, timeout=15) as resp:
                response_data = json.loads(resp.read().decode('utf-8'))
                print(f"API: Stream created successfully. ID: {response_data.get('id')}")
                return response_data
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            print(f"API Error {e.code}: {err_body}")
            raise e
        except Exception as e:
            print(f"API Connection Error: {e}")
            raise e

    def update_stream(self, stream_id, model_id, **params):
        if not stream_id or not model_id:
            print("API Warning: Missing stream_id or model_id for update")
            return
        url = f"{self.BASE_URL}/streams/{stream_id}"
        payload = {
            "pipeline": "streamdiffusion",
            "params": {"model_id": model_id, **params}
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=self._get_headers(), method="PATCH")
        try:
            with self._opener.open(req, timeout=10) as resp:
                return True
        except Exception as e:
            print(f"API Update Error: {e}")
            return False

    def exchange_sdp(self, url, offer_sdp, token=None, timeout=5):
        headers = {"Content-Type": "application/sdp"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = offer_sdp.encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8'), dict(resp.getheaders())
        except urllib.error.HTTPError as e:
            raise e
        except Exception as e:
            print(f"API SDP Exchange Error: {e}")
            raise e

    def create_api_key(self, jwt_token, name="TouchDesigner", user_type="touchdesigner"):
        url = f"{self.BASE_URL}/api-key"
        payload = {"name": name, "user_type": user_type}
        data = json.dumps(payload).encode('utf-8')
        headers = {"Authorization": f"Bearer {jwt_token}", "Content-Type": "application/json", "x-client-source": "touchdesigner"}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with self._opener.open(req, timeout=10) as resp:
                return json.loads(resp.read().decode('utf-8')).get('apiKey')
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            print(f"API Error creating key {e.code}: {err_body}")
            raise e


class ParameterManager:
    def __init__(self, owner_comp):
        self.ownerComp = owner_comp
        self._style_image_cache = {'source': None, 'signature': None, 'data': None}

    def _get(self, name, default=None):
        if hasattr(self.ownerComp.par, name):
            return getattr(self.ownerComp.par, name).eval()
        return default if default is not None else PARAM_DEFAULTS.get(name)

    def _get_int(self, name, default=None):
        val = self._get(name, default)
        return int(val) if val is not None else default

    def _get_bool(self, name, default=None):
        val = self._get(name, default)
        return bool(val) if val is not None else default

    @property
    def Prompt(self):
        return self._get('Prompt', '')

    @property
    def Negprompt(self):
        return self._get('Negprompt', '')

    @property
    def Seed(self):
        return self._get_int('Seed', -1)

    @property
    def Guidance(self):
        return self._get('Guidance', 1.0)

    @property
    def Delta(self):
        return self._get('Delta', 0.7)

    @property
    def Steps(self):
        return self._get_int('Steps', 50)

    @property
    def Noise(self):
        return self._get_bool('Noise', True)

    @property
    def Width(self):
        return self._get_int('Width', 512)

    @property
    def Height(self):
        return self._get_int('Height', 512)

    @property
    def Depth(self):
        return self._get('Depth', 0.0)

    @property
    def Canny(self):
        return self._get('Canny', 0.0)

    @property
    def Tile(self):
        return self._get('Tile', 0.0)

    @property
    def Hed(self):
        return self._get('Hed', 0.0)

    @property
    def Openpose(self):
        return self._get('Openpose', 0.0)

    @property
    def Color(self):
        return self._get('Color', 0.0)

    @property
    def Ipadapter(self):
        return self._get_bool('Ipadapter', False)

    @property
    def Ipadapterscale(self):
        return self._get('Ipadapterscale', 0.5)

    @property
    def Styleimage(self):
        return self._get('Styleimage', '')

    @property
    def Ipadaptertype(self):
        return self._get('Ipadaptertype', 'regular')

    @property
    def Model(self):
        return self._get('Model', 'stabilityai/sdxl-turbo')

    @property
    def Active(self):
        return self._get_bool('Active', False)

    @property
    def TindexList(self):
        result = []
        if hasattr(self.ownerComp.seq, 'Stepschedule'):
            for block in self.ownerComp.seq.Stepschedule.blocks:
                if hasattr(block.par, 'Step'):
                    val = block.par.Step.eval()
                    if val >= 0:
                        result.append(int(val))
        return result if result else [11]

    def _get_page(self, name):
        for p in self.ownerComp.customPages:
            if p.name == name:
                return p
        return None

    def setup(self):
        daydream_page = self._get_page('Daydream')
        params_page = self._get_page('Parameters')
        if not daydream_page or not params_page:
            self.create_all()
            return
        self._ensure_missing_control_params(daydream_page)
        self._ensure_missing_params(params_page)

    def _ensure_missing_control_params(self, page):
        if not hasattr(self.ownerComp.par, 'Login'):
            page.appendPulse('Login', label='Login')
        if not hasattr(self.ownerComp.par, 'Active'):
            p = page.appendToggle('Active', label='Active')[0]
            p.default = p.val = False
        if not hasattr(self.ownerComp.par, 'Model'):
            self._create_model_param(page)
        if not hasattr(self.ownerComp.par, 'Resetparameters'):
            page.appendPulse('Resetparameters', label='Reset Parameters')

    def _ensure_missing_params(self, page):
        if not hasattr(self.ownerComp.par, 'Prompt'):
            p = page.appendStr('Prompt', label='Prompt')[0]
            p.default = p.val = PARAM_DEFAULTS['Prompt']
        if not hasattr(self.ownerComp.par, 'Negprompt'):
            p = page.appendStr('Negprompt', label='Negative Prompt')[0]
            p.default = p.val = PARAM_DEFAULTS['Negprompt']
        if not hasattr(self.ownerComp.par, 'Seed'):
            self._create_seed_param(page)
        if not hasattr(self.ownerComp.par, 'Noise'):
            p = page.appendToggle('Noise', label='Add Noise')[0]
            p.default = p.val = True
        if not hasattr(self.ownerComp.par, 'Guidance'):
            self._create_guidance_param(page)
        if not hasattr(self.ownerComp.par, 'Delta'):
            self._create_delta_param(page)
        if not hasattr(self.ownerComp.par, 'Steps'):
            self._create_steps_param(page)
        if not hasattr(self.ownerComp.seq, 'Stepschedule'):
            self._create_stepschedule(page)
        if not hasattr(self.ownerComp.par, 'Width'):
            self._create_resolution_param(page, 'Width')
        if not hasattr(self.ownerComp.par, 'Height'):
            self._create_resolution_param(page, 'Height')
        for cn_type, par_name in CONTROLNET_PARAM_MAP.items():
            if not hasattr(self.ownerComp.par, par_name):
                self._create_controlnet_param(page, par_name)
        if not hasattr(self.ownerComp.par, 'Ipadapter'):
            p = page.appendToggle('Ipadapter', label='IP Adapter')[0]
            p.default = p.val = True
        if not hasattr(self.ownerComp.par, 'Ipadapterscale'):
            self._create_ipadapter_scale_param(page)
        if not hasattr(self.ownerComp.par, 'Ipadaptertype'):
            self._create_ipadapter_type_param(page)
        if not hasattr(self.ownerComp.par, 'Styleimage'):
            page.appendStr('Styleimage', label='Style Image')

    def create_all(self):
        daydream = self.ownerComp.appendCustomPage('Daydream')
        daydream.appendHeader('Controls')
        daydream.appendPulse('Login', label='Login')
        p = daydream.appendToggle('Active', label='Active')[0]
        p.default = p.val = False
        self._create_model_param(daydream)
        daydream.appendPulse('Resetparameters', label='Reset Parameters')

        params = self.ownerComp.appendCustomPage('Parameters')

        params.appendHeader('Generation')
        p = params.appendStr('Prompt', label='Prompt')[0]
        p.default = p.val = PARAM_DEFAULTS['Prompt']
        p = params.appendStr('Negprompt', label='Negative Prompt')[0]
        p.default = p.val = PARAM_DEFAULTS['Negprompt']
        self._create_seed_param(params)
        p = params.appendToggle('Noise', label='Add Noise')[0]
        p.default = p.val = True

        params.appendHeader('Diffusion')
        self._create_guidance_param(params)
        self._create_delta_param(params)
        self._create_steps_param(params)
        self._create_stepschedule(params)

        params.appendHeader('Resolution')
        self._create_resolution_param(params, 'Width')
        self._create_resolution_param(params, 'Height')

        params.appendHeader('Controlnet')
        for cn_type, par_name in CONTROLNET_PARAM_MAP.items():
            self._create_controlnet_param(params, par_name)

        params.appendHeader('Style')
        p = params.appendToggle('Ipadapter', label='IP Adapter')[0]
        p.default = p.val = True
        self._create_ipadapter_scale_param(params)
        self._create_ipadapter_type_param(params)
        params.appendStr('Styleimage', label='Style Image')

    def _create_model_param(self, page):
        p = page.appendMenu('Model', label='Model')[0]
        p.menuNames = ['stabilityai/sdxl-turbo', 'stabilityai/sd-turbo', 'Lykon/dreamshaper-8', 'prompthero/openjourney-v4']
        p.menuLabels = ['SDXL Turbo', 'SD Turbo', 'Dreamshaper 8', 'Openjourney v4']
        p.default = p.val = 'stabilityai/sdxl-turbo'

    def _create_seed_param(self, page):
        p = page.appendInt('Seed', label='Seed')[0]
        p.default = p.val = 42
        p.min = -1
        p.normMin, p.normMax = 0, 10000
        p.clampMin = True

    def _create_guidance_param(self, page):
        p = page.appendFloat('Guidance', label='Guidance Scale')[0]
        p.default = p.val = 1.0
        p.min, p.max = 0.1, 20.0
        p.clampMin = True

    def _create_delta_param(self, page):
        p = page.appendFloat('Delta', label='Delta')[0]
        p.default = p.val = 0.7
        p.min, p.max = 0.0, 1.0
        p.clampMin = p.clampMax = True

    def _create_steps_param(self, page):
        p = page.appendInt('Steps', label='Inference Steps')[0]
        p.default = p.val = 50
        p.min, p.max = 1, 100
        p.normMin, p.normMax = 1, 100
        p.clampMin = p.clampMax = True

    def _create_stepschedule(self, page):
        page.appendSequence('Stepschedule', label='Step Schedule')
        p = page.appendInt('Step', label='Step')[0]
        p.default = p.val = 11
        p.min, p.max = 0, 50
        p.normMin, p.normMax = 0, 50
        p.clampMin = True
        self.ownerComp.seq.Stepschedule.blockSize = 1

    def _create_resolution_param(self, page, name):
        p = page.appendMenu(name, label=name)[0]
        p.menuNames = p.menuLabels = ['512', '448', '384', '320', '256', '192', '128', '64']
        p.default = p.val = '512'

    def _create_controlnet_param(self, page, par_name):
        p = page.appendFloat(par_name, label=par_name)[0]
        p.default = p.val = PARAM_DEFAULTS.get(par_name, 0.0)
        p.min, p.max = 0.0, 1.0
        p.clampMin = p.clampMax = True

    def _create_ipadapter_scale_param(self, page):
        p = page.appendFloat('Ipadapterscale', label='IP Adapter Scale')[0]
        p.default = p.val = 0.5
        p.min, p.max = 0.0, 1.0
        p.clampMin = p.clampMax = True

    def _create_ipadapter_type_param(self, page):
        p = page.appendMenu('Ipadaptertype', label='IP Adapter Type')[0]
        p.menuNames = ['regular', 'faceid']
        p.menuLabels = ['Regular', 'FaceID']
        p.default = p.val = 'regular'

    def reset(self):
        for p in list(self.ownerComp.customPages):
            if p.name in ('Daydream', 'Parameters'):
                p.destroy()
        if hasattr(self.ownerComp.seq, 'Stepschedule'):
            self.ownerComp.seq.Stepschedule.destroy()
        self.create_all()
        print("Daydream: Parameters reset to defaults")

    def update_states(self, logged_in):
        par = self.ownerComp.par
        all_params = [
            'Resetparameters', 'Active', 'Model', 'Prompt', 'Negprompt', 'Seed', 'Noise',
            'Guidance', 'Delta', 'Steps', 'Stepschedule', 'Width', 'Height',
            'Depth', 'Canny', 'Tile', 'Hed', 'Openpose', 'Color',
            'Ipadapter', 'Ipadapterscale', 'Ipadaptertype', 'Styleimage',
        ]
        for par_name in all_params:
            if hasattr(par, par_name):
                getattr(par, par_name).enable = logged_in
        if hasattr(self.ownerComp.seq, 'Stepschedule'):
            for block in self.ownerComp.seq.Stepschedule.blocks:
                if hasattr(block.par, 'Step'):
                    block.par.Step.enable = logged_in
        if not logged_in:
            if hasattr(par, 'Active'):
                par.Active.val = False
            return
        self.update_controlnet_states()
        self.update_ipadapter_states()

    def update_controlnet_states(self):
        model = self.Model
        available = set(CONTROLNET_SUPPORT.get(model, {}).keys())
        par = self.ownerComp.par
        for cn_type, par_name in CONTROLNET_PARAM_MAP.items():
            if hasattr(par, par_name):
                p = getattr(par, par_name)
                p.enable = cn_type in available
                if cn_type not in available:
                    p.val = 0

    def update_ipadapter_states(self):
        model = self.Model
        supported_types = IP_ADAPTER_SUPPORT.get(model, set())
        has_ip_adapter = len(supported_types) > 0
        has_faceid = "faceid" in supported_types
        par = self.ownerComp.par
        for par_name in ['Ipadapter', 'Ipadapterscale', 'Styleimage']:
            if hasattr(par, par_name):
                getattr(par, par_name).enable = has_ip_adapter
        if hasattr(par, 'Ipadaptertype'):
            par.Ipadaptertype.enable = has_faceid
            if not has_faceid:
                par.Ipadaptertype.val = 'regular'

    def update_cold_states(self, is_streaming):
        par = self.ownerComp.par
        cold_params = ['Resetparameters', 'Model', 'Width', 'Height', 'Steps', 'Noise', 'Ipadaptertype']
        for par_name in cold_params:
            if hasattr(par, par_name):
                getattr(par, par_name).enable = not is_streaming

    def setup_param_exec(self):
        param_exec = self.ownerComp.op('param_exec')
        if param_exec and hasattr(param_exec.par, 'pars'):
            param_exec.par.pars = ' '.join(ALL_WATCHED_PARAMS)

    def get_style_image_source(self):
        value = self.Styleimage
        if not value:
            self._style_image_cache = {'source': None, 'signature': None, 'data': None}
            return None
        if value.startswith('http://') or value.startswith('https://'):
            return value
        style_top = op(value)
        if not style_top or not hasattr(style_top, 'saveByteArray') or style_top.width == 0:
            return None
        signature = (style_top.width, style_top.height, style_top.time.frame)
        cached = self._style_image_cache
        if cached.get('source') == value and cached.get('signature') == signature and cached.get('data'):
            return cached['data']
        jpeg_data = style_top.saveByteArray('.jpg', quality=0.85)
        if len(jpeg_data) > 50 * 1024 * 1024:
            print("Daydream Warning: Style image too large (>50MB)")
            return None
        data_url = f"data:image/jpeg;base64,{base64.b64encode(jpeg_data).decode('ascii')}"
        self._style_image_cache = {'source': value, 'signature': signature, 'data': data_url}
        return data_url

    def invalidate_style_cache(self):
        self._style_image_cache = {'source': None, 'signature': None, 'data': None}

    def build_controlnets(self):
        model = self.Model
        support = CONTROLNET_SUPPORT.get(model, {})
        if not support:
            return None
        controlnets = []
        scale_map = [
            ("depth", self.Depth), ("canny", self.Canny), ("tile", self.Tile),
            ("hed", self.Hed), ("openpose", self.Openpose), ("color", self.Color),
        ]
        for cn_type, scale in scale_map:
            if cn_type not in support:
                continue
            model_id, preprocessor = support[cn_type]
            controlnets.append({
                "model_id": model_id,
                "conditioning_scale": scale,
                "preprocessor": preprocessor,
                "preprocessor_params": {},
                "enabled": True
            })
        return controlnets if controlnets else None

    def build_ip_adapter(self, has_style_image=False):
        return {
            "type": self.Ipadaptertype,
            "enabled": self.Ipadapter and has_style_image,
            "scale": self.Ipadapterscale,
        }

    def build_params(self, for_update=False):
        params = {
            "prompt": self.Prompt,
            "negative_prompt": self.Negprompt,
            "guidance_scale": self.Guidance,
            "delta": self.Delta,
            "t_index_list": self.TindexList,
            "do_add_noise": self.Noise,
        }
        seed = self.Seed
        if seed >= 0:
            params["seed"] = seed
        controlnets = self.build_controlnets()
        if controlnets:
            params["controlnets"] = controlnets
        if IP_ADAPTER_SUPPORT.get(self.Model):
            style_source = self.get_style_image_source()
            params["ip_adapter"] = self.build_ip_adapter(has_style_image=style_source is not None)
            if style_source:
                params["ip_adapter_style_image_url"] = style_source
        if not for_update:
            params["width"] = self.Width
            params["height"] = self.Height
            params["num_inference_steps"] = self.Steps
        return params

    def build_changed_params(self, changed):
        params = {}
        if 'Prompt' in changed:
            params['prompt'] = self.Prompt
        if 'Negprompt' in changed:
            params['negative_prompt'] = self.Negprompt
        if 'Seed' in changed:
            seed = self.Seed
            if seed >= 0:
                params['seed'] = seed
        if 'Guidance' in changed:
            params['guidance_scale'] = self.Guidance
        if 'Delta' in changed:
            params['delta'] = self.Delta
        if 'Noise' in changed:
            params['do_add_noise'] = self.Noise
        if any(c.lower().startswith('stepschedule') for c in changed):
            params['t_index_list'] = self.TindexList
        if changed & CN_PARAMS_SET:
            controlnets = self.build_controlnets()
            if controlnets:
                params['controlnets'] = controlnets
        if changed & IP_PARAMS_SET and IP_ADAPTER_SUPPORT.get(self.Model):
            style_source = self.get_style_image_source()
            params['ip_adapter'] = self.build_ip_adapter(has_style_image=style_source is not None)
            if style_source:
                params['ip_adapter_style_image_url'] = style_source
        return params


class HTTPHandler:
    def __init__(self, ext):
        self.ext = ext

    def handle(self, request, response, server_type='frame'):
        path = request.get('uri', '/').split('?')[0]
        method = request.get('method', 'GET')

        if server_type == 'sdp':
            self._add_cors_headers(response)
            if method == 'OPTIONS':
                response['statusCode'] = 204
                response['statusReason'] = 'No Content'
                response['data'] = b''
            elif path == '/whip' and method == 'POST':
                self._handle_whip_proxy(request, response)
            elif path.startswith('/whip/result/') and method == 'GET':
                self._handle_whip_result(response, path)
            elif path == '/whep' and method == 'POST':
                self._handle_whep_proxy(request, response)
            elif path.startswith('/whep/result/') and method == 'GET':
                self._handle_whep_result(response, path)
            else:
                response['statusCode'] = 404
                response['data'] = b'Not Found'
        elif server_type == 'auth':
            self._handle_auth_callback(request, response)
        else:
            if path == '/relay.html' and method == 'GET':
                self._handle_relay_html(response)
            elif path == '/status' and method == 'GET':
                self._handle_status_request(response)
            else:
                response['statusCode'] = 404
                response['data'] = b'Not Found'

    def _add_cors_headers(self, response):
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type'

    def _handle_relay_html(self, response):
        response['statusCode'] = 200
        response['statusReason'] = 'OK'
        response['content-type'] = 'text/html; charset=utf-8'
        response['data'] = self.ext._get_relay_html()

    def _handle_status_request(self, response):
        status = {
            'state': self.ext.state,
            'stream_id': self.ext.stream_id,
            'whip_url': self.ext.whip_url,
            'whep_url': self.ext.whep_url
        }
        response['statusCode'] = 200
        response['content-type'] = 'application/json'
        response['data'] = json.dumps(status).encode()

    def _handle_whip_proxy(self, request, response):
        if not self.ext.whip_url:
            response['statusCode'] = 400
            response['data'] = b'No WHIP URL available'
            return
        offer_sdp = request.get('data', b'').decode('utf-8')
        request_id = secrets.token_urlsafe(8)
        print(f"Daydream: WHIP proxy - forwarding offer to {self.ext.whip_url}")
        owner_path = self.ext.ownerComp.path
        self.ext._whip_requests[request_id] = {
            'status': 'pending',
            'offer': offer_sdp,
            'answer': None,
            'error': None,
            'whip_url': self.ext.whip_url,
            'token': self.ext.ApiToken
        }
        ext = self.ext
        def exchange_async():
            req_data = ext._whip_requests.get(request_id)
            if not req_data:
                return
            try:
                answer_sdp, headers = ext.api.exchange_sdp(req_data['whip_url'], req_data['offer'], req_data['token'], timeout=10)
                for k, v in headers.items():
                    if k.lower() == 'livepeer-playback-url':
                        ext.whep_url = v
                        print(f"Daydream: Got WHEP URL: {ext.whep_url}")
                        break
                req_data['answer'] = answer_sdp
                req_data['status'] = 'ready'
            except urllib.error.HTTPError as e:
                err_body = e.read().decode() if hasattr(e, 'read') else str(e)
                print(f"Daydream: WHIP proxy error {e.code}: {err_body}")
                req_data['status'] = 'error'
                req_data['error'] = err_body
                run(f"op('{owner_path}').ext.Daydream._onWhipFailed()", delayFrames=1)
            except Exception as e:
                print(f"Daydream: WHIP proxy error: {e}")
                req_data['status'] = 'error'
                req_data['error'] = str(e)
                run(f"op('{owner_path}').ext.Daydream._onWhipFailed()", delayFrames=1)
        ext._executor.submit(exchange_async)
        response['statusCode'] = 202
        response['statusReason'] = 'Accepted'
        response['content-type'] = 'application/json'
        response['data'] = json.dumps({'id': request_id}).encode('utf-8')

    def _handle_whip_result(self, response, path):
        request_id = path.split('/whip/result/')[-1]
        req_data = self.ext._whip_requests.get(request_id)
        if not req_data:
            response['statusCode'] = 404
            response['data'] = b'Request not found'
            return
        if req_data['status'] == 'pending':
            response['statusCode'] = 202
            response['content-type'] = 'application/json'
            response['data'] = json.dumps({'status': 'pending'}).encode('utf-8')
        elif req_data['status'] == 'ready':
            response['statusCode'] = 200
            response['content-type'] = 'application/sdp'
            response['data'] = req_data['answer'].encode('utf-8')
            del self.ext._whip_requests[request_id]
        else:
            response['statusCode'] = 500
            response['data'] = (req_data['error'] or 'Unknown error').encode('utf-8')
            del self.ext._whip_requests[request_id]

    def _handle_whep_proxy(self, request, response):
        if not self.ext.whep_url:
            response['statusCode'] = 404
            response['data'] = b'No WHEP URL available yet'
            return
        offer_sdp = request.get('data', b'').decode('utf-8')
        request_id = secrets.token_urlsafe(8)
        self.ext._whep_requests[request_id] = {
            'status': 'pending',
            'offer': offer_sdp,
            'answer': None,
            'error': None,
            'whep_url': self.ext.whep_url
        }
        ext = self.ext
        def exchange_async():
            req_data = ext._whep_requests.get(request_id)
            if not req_data:
                return
            try:
                answer_sdp, _ = ext.api.exchange_sdp(req_data['whep_url'], req_data['offer'], timeout=5)
                req_data['answer'] = answer_sdp
                req_data['status'] = 'ready'
            except urllib.error.HTTPError:
                req_data['status'] = 'error'
                req_data['error'] = 'WHEP not ready'
            except Exception as e:
                req_data['status'] = 'error'
                req_data['error'] = str(e)
        ext._executor.submit(exchange_async)
        response['statusCode'] = 202
        response['statusReason'] = 'Accepted'
        response['content-type'] = 'application/json'
        response['data'] = json.dumps({'id': request_id}).encode('utf-8')

    def _handle_whep_result(self, response, path):
        request_id = path.split('/whep/result/')[-1]
        req_data = self.ext._whep_requests.get(request_id)
        if not req_data:
            response['statusCode'] = 404
            response['data'] = b'Request not found'
            return
        if req_data['status'] == 'pending':
            response['statusCode'] = 202
            response['content-type'] = 'application/json'
            response['data'] = json.dumps({'status': 'pending'}).encode('utf-8')
        elif req_data['status'] == 'ready':
            response['statusCode'] = 200
            response['content-type'] = 'application/sdp'
            response['data'] = req_data['answer'].encode('utf-8')
            del self.ext._whep_requests[request_id]
        else:
            response['statusCode'] = 500
            response['data'] = (req_data['error'] or 'Unknown error').encode('utf-8')
            del self.ext._whep_requests[request_id]

    def _handle_auth_callback(self, request, response):
        params = request.get('pars', {})
        print(f"Daydream: Auth callback received, params: {params}")
        token = params.get('token')
        state = params.get('state')
        if not token:
            err = "No token received"
            response['statusCode'] = 400
            response['content-type'] = 'text/html; charset=utf-8'
            response['data'] = f'<html><body><h1>Error: {err}</h1></body></html>'.encode('utf-8')
            self.ext._emit('login_failed', {'error': err})
            self.ext._emit('error', {'error': err, 'context': 'login'})
            return
        if not self.ext._consume_auth_state(state):
            err = "Invalid state parameter"
            response['statusCode'] = 400
            response['content-type'] = 'text/html; charset=utf-8'
            response['data'] = f'<html><body><h1>Error: {err}</h1></body></html>'.encode('utf-8')
            self.ext._auth_pending = False
            self.ext._emit('login_failed', {'error': err})
            self.ext._emit('error', {'error': err, 'context': 'login'})
            return
        try:
            api_key = self.ext.api.create_api_key(token)
            if not api_key:
                raise ValueError("No API key returned")
            self.ext._api_key = api_key
            self.ext._saveCredentials(api_key)
            print("Daydream: Login successful, API key saved")
            run(f"op('{self.ext.ownerComp.path}').ext.Daydream._onLoginSuccess()", delayFrames=1)
            response['statusCode'] = 302
            response['statusReason'] = 'Found'
            response['Location'] = 'https://app.daydream.monster/sign-in/local/success'
            response['data'] = b''
        except Exception as e:
            err = str(e)
            print(f"Daydream: Login failed: {err}")
            response['statusCode'] = 500
            response['content-type'] = 'text/html; charset=utf-8'
            response['data'] = f'<html><body><h1>Error: {err}</h1></body></html>'.encode('utf-8')
            self.ext._emit('login_failed', {'error': err})
            self.ext._emit('error', {'error': err, 'context': 'login'})
        finally:
            self.ext._auth_pending = False


class DaydreamExt:
    CREDENTIALS_PATH = os.path.expanduser("~/.daydream/credentials")
    AUTH_STATES_PATH = os.path.expanduser("~/.daydream/auth_states.json")
    AUTH_STATE_TTL = 300

    def __init__(self, ownerComp):
        self.ownerComp = ownerComp
        self.api = DaydreamAPI()
        self.params = ParameterManager(ownerComp)
        self.http = HTTPHandler(self)

        self._listeners = []
        self.state = "IDLE"
        self.stream_id = None
        self.model_id = None
        self.whip_url = None
        self.whep_url = None

        self.mjpeg_port, self.sdp_port, self.auth_port = self._allocate_ports()

        self.ws_clients = set()
        self._whip_requests = {}
        self._whep_requests = {}

        self._auth_state = None
        self._auth_pending = False
        self._api_key = None

        self._stream_source = None
        self._web_server = None

        self._executor = ThreadPoolExecutor(max_workers=4)
        self._relay_html_cache = None
        self._pending_changes = set()
        self._params_update_scheduled = False

        self._loadCredentials()
        self.params.setup()
        if hasattr(self.ownerComp.par, 'Active'):
            self.ownerComp.par.Active.val = False
        self.params.update_states(self.IsLoggedIn)
        self.params.setup_param_exec()
        self._startServers()
        self._warmupWebRender()

        if self._api_key:
            print(f"DaydreamExt v{VERSION} initialized (Logged in)")
        else:
            print(f"DaydreamExt v{VERSION} initialized (Not logged in - click Login)")

        self._emit('initialized', {'logged_in': self.IsLoggedIn})
    @property
    def Prompt(self):
        return self.params.Prompt

    @property
    def Active(self):
        return self.params.Active

    @property
    def Model(self):
        return self.params.Model

    @property
    def ApiToken(self):
        return self._api_key or ""

    @property
    def IsLoggedIn(self):
        return bool(self._api_key)

    def register_listener(self, fn):
        if callable(fn) and fn not in self._listeners:
            self._listeners.append(fn)

    def unregister_listener(self, fn):
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _emit(self, event, payload=None):
        if payload is None:
            payload = {}
        payload['owner_path'] = self.ownerComp.path
        payload['state'] = self.state
        payload['stream_id'] = self.stream_id
        for listener in self._listeners:
            try:
                listener(event, payload)
            except Exception as e:
                print(f"Daydream: Listener error on '{event}': {e}")

    def _set_state(self, new_state, reason=None, error=None):
        old_state = self.state
        if old_state == new_state:
            return
        self.state = new_state
        payload = {'from': old_state, 'to': new_state}
        if reason:
            payload['reason'] = reason
        if error:
            payload['error'] = str(error)
        self._emit('state_changed', payload)

    def GetCapabilities(self):
        model = self.Model
        return {
            'backend': 'daydream',
            'version': VERSION,
            'model': model,
            'supported_models': list(CONTROLNET_SUPPORT.keys()),
            'controlnets': list(CONTROLNET_SUPPORT.get(model, {}).keys()),
            'ip_adapter_types': list(IP_ADAPTER_SUPPORT.get(model, set())),
        }

    def _allocate_ports(self):
        ports = []
        sockets = []
        for _ in range(3):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('127.0.0.1', 0))
            ports.append(s.getsockname()[1])
            sockets.append(s)
        for s in sockets:
            s.close()
        return tuple(ports)

    def _loadCredentials(self):
        if not os.path.exists(self.CREDENTIALS_PATH):
            return
        try:
            with open(self.CREDENTIALS_PATH, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('DAYDREAM_API_KEY:'):
                        self._api_key = line.split(':', 1)[1].strip()
                        break
                if self._api_key:
                    print(f"Daydream: Loaded credentials from {self.CREDENTIALS_PATH}")
        except Exception as e:
            print(f"Daydream: Failed to load credentials: {e}")

    def _saveCredentials(self, api_key):
        credentials_dir = os.path.dirname(self.CREDENTIALS_PATH)
        if not os.path.exists(credentials_dir):
            os.makedirs(credentials_dir)
        try:
            with open(self.CREDENTIALS_PATH, 'w') as f:
                f.write(f"DAYDREAM_API_KEY: {api_key}\n")
            print(f"Daydream: Saved credentials to {self.CREDENTIALS_PATH}")
        except Exception as e:
            print(f"Daydream: Failed to save credentials: {e}")

    def _load_auth_states(self):
        import time
        if not os.path.exists(self.AUTH_STATES_PATH):
            return {}
        try:
            with open(self.AUTH_STATES_PATH, 'r') as f:
                data = json.load(f)
            states = data.get('states', {})
            now = time.time()
            return {s: t for s, t in states.items() if now - t < self.AUTH_STATE_TTL}
        except Exception:
            return {}

    def _save_auth_states(self, states):
        auth_dir = os.path.dirname(self.AUTH_STATES_PATH)
        if not os.path.exists(auth_dir):
            os.makedirs(auth_dir)
        try:
            with open(self.AUTH_STATES_PATH, 'w') as f:
                json.dump({'states': states}, f)
        except Exception as e:
            print(f"Daydream: Failed to save auth states: {e}")

    def _add_auth_state(self, state):
        import time
        states = self._load_auth_states()
        states[state] = time.time()
        self._save_auth_states(states)

    def _consume_auth_state(self, state):
        if not state:
            return False
        states = self._load_auth_states()
        if state not in states:
            return False
        del states[state]
        self._save_auth_states(states)
        return True

    def _onLoginSuccess(self):
        self.params.update_states(True)
        print("Daydream: Login successful, ready to stream")
        self._emit('login_success', {})

    def _resetStreamState(self, reason=None):
        self.stream_id = None
        self.whip_url = None
        self.model_id = None
        self.whep_url = None
        self._set_state("IDLE", reason=reason or "reset")

    def ResetParameters(self):
        self.params.reset()
        self.params.update_states(self.IsLoggedIn)
        self.params.update_controlnet_states()
        self.params.setup_param_exec()

    def Setup(self):
        required_ops = ['web_server', 'web_server_sdp', 'web_render', 'stream_source', 'frame_timer']
        missing = [op for op in required_ops if not self.ownerComp.op(op)]
        if missing:
            print(f"Daydream Warning: Missing operators: {missing}")
            return False
        return True

    def Login(self):
        self._auth_state = secrets.token_urlsafe(16)
        self._add_auth_state(self._auth_state)
        self._auth_pending = True
        auth_url = f"https://app.daydream.live/sign-in/local?port={self.auth_port}&state={self._auth_state}"
        print(f"Daydream: Opening browser for login: {auth_url}")
        self._emit('login_started', {'auth_port': self.auth_port})
        webbrowser.open(auth_url)

    def Start(self):
        if not self.ApiToken:
            err = "Not logged in. Please click Login first."
            print(f"Daydream Error: {err}")
            self._set_state("ERROR", reason="start_failed", error=err)
            self._emit('error', {'error': err, 'context': 'start'})
            return
        self._stream_source = self.ownerComp.op('stream_source')
        if not self._stream_source or self._stream_source.width == 0 or self._stream_source.height == 0:
            err = "No input connected to stream_source."
            print(f"Daydream Error: {err}")
            self._set_state("ERROR", reason="start_failed", error=err)
            self._emit('error', {'error': err, 'context': 'start'})
            return
        if self.state == "CREATING":
            print("Daydream: Stream is being created, please wait...")
            return
        self._web_server = self.ownerComp.op('web_server')
        self._setupWebRender()
        frame_timer = self.ownerComp.op('frame_timer')
        if frame_timer:
            frame_timer.par.active = 1
        self._createStream()

    def Stop(self):
        print("Daydream: Stopping...")
        was_streaming = self.state == "STREAMING"
        prev_stream_id = self.stream_id
        frame_timer = self.ownerComp.op('frame_timer')
        if frame_timer:
            frame_timer.par.active = 0
        self._params_update_scheduled = False
        self._pending_changes.clear()
        web_server = self.ownerComp.op('web_server')
        if web_server:
            for client in list(self.ws_clients):
                try:
                    web_server.webSocketClose(client)
                except:
                    pass
        self.ws_clients.clear()
        self._whip_requests.clear()
        self._whep_requests.clear()
        web_render = self.ownerComp.op('web_render')
        if web_render:
            web_render.par.url = 'about:blank'
        self._stream_source = None
        self._web_server = None
        self._resetStreamState(reason="stop")
        self.params.update_cold_states(False)
        self.UpdateStatusText("Idle")
        if was_streaming:
            self._emit('streaming_stopped', {'prev_stream_id': prev_stream_id})

    def _warmupWebRender(self):
        web_render = self.ownerComp.op('web_render')
        if not web_render:
            return
        web_render.par.url = 'about:blank'
        web_render.par.active = 1
        print("Daydream: Web Render pre-warmed")

    def _startServers(self):
        web_server = self.ownerComp.op('web_server')
        if web_server:
            web_server.par.active = 0
            web_server.par.port = self.mjpeg_port
            web_server.par.active = 1
            print(f"Daydream: Frame server started on port {self.mjpeg_port}")
        else:
            print("Daydream Error: web_server DAT not found")
        web_server_sdp = self.ownerComp.op('web_server_sdp')
        if web_server_sdp:
            web_server_sdp.par.active = 0
            web_server_sdp.par.port = self.sdp_port
            web_server_sdp.par.active = 1
            print(f"Daydream: SDP proxy server started on port {self.sdp_port}")
        else:
            print("Daydream Error: web_server_sdp DAT not found - WHIP/WHEP will not work!")
        web_server_auth = self.ownerComp.op('web_server_auth')
        if web_server_auth:
            web_server_auth.par.active = 0
            web_server_auth.par.port = self.auth_port
            web_server_auth.par.active = 1
            print(f"Daydream: Auth server started on port {self.auth_port}")
        else:
            print("Daydream Error: web_server_auth DAT not found")

    def _setupWebRender(self):
        web_render = self.ownerComp.op('web_render')
        if not web_render:
            print("Daydream Error: web_render TOP not found")
            return
        url = f"http://localhost:{self.mjpeg_port}/relay.html"
        print(f"Daydream: Loading Web Render URL: {url}")
        web_render.par.url = url

    def _createStream(self):
        if self.state == "CREATING":
            print("Daydream: Stream creation already in progress")
            return
        if not self.ApiToken:
            print("Daydream Error: Not logged in")
            return
        print("Daydream: Creating stream...")
        self._set_state("CREATING", reason="stream_create")
        self._emit('stream_create_started', {'model': self.params.Model})
        self.UpdateStatusText("Creating stream...")
        self.api.set_token(self.ApiToken)
        params = self.params.build_params(for_update=False)
        self._start_params = {
            "model": self.params.Model,
            "params": params,
            "owner_path": self.ownerComp.path
        }
        self._executor.submit(self._createStreamAsync)

    def _createStreamAsync(self):
        owner_path = self._start_params["owner_path"]
        try:
            params = self._start_params["params"]
            response = self.api.create_stream(model_id=self._start_params["model"], **params)
            self._pending_response = response
            run(f"op('{owner_path}').ext.Daydream._onStreamCreated()", delayFrames=1)
        except Exception as e:
            self._pending_error = str(e)
            run(f"op('{owner_path}').ext.Daydream._onStreamCreateError()", delayFrames=1)

    def _onStreamCreated(self):
        response = self._pending_response
        self.stream_id = response.get("id")
        self.whip_url = response.get("whip_url")
        response_params = response.get("params", {})
        self.model_id = response_params.get("model_id")
        print(f"Daydream: Stream Created. ID: {self.stream_id}")
        print(f"Daydream: WHIP URL: {self.whip_url}")
        print(f"Daydream: Model: {self.model_id}")
        self._emit('stream_created', {
            'whip_url': self.whip_url,
            'model_id': self.model_id,
        })
        if self.Active:
            self._startWebRTC()
        else:
            self._resetStreamState(reason="active_toggled_off")
            self.UpdateStatusText("Idle")

    def _onStreamCreateError(self):
        err = self._pending_error
        print(f"Daydream Error: Failed to create stream. {err}")
        self._resetStreamState(reason="stream_create_failed")
        self._set_state("ERROR", reason="stream_create_failed", error=err)
        self._emit('stream_create_failed', {'error': err})
        self._emit('error', {'error': err, 'context': 'stream_create'})
        self.UpdateStatusText(f"Error: {err}")
        if hasattr(self.ownerComp.par, 'Active'):
            self.ownerComp.par.Active.val = False

    def _onWhipFailed(self):
        print("Daydream: WHIP failed, recreating stream...")
        self._emit('error', {'error': 'WHIP connection failed', 'context': 'whip', 'will_retry': self.Active})
        self._resetStreamState(reason="whip_failed")
        if self.Active:
            self._createStream()

    def _startWebRTC(self):
        print("Daydream: Stream ready, WebRTC can connect...")
        self._set_state("STREAMING", reason="webrtc_ready")
        self._emit('streaming_started', {
            'whip_url': self.whip_url,
            'whep_url': self.whep_url,
            'model_id': self.model_id,
        })
        self.UpdateStatusText(f"Streaming: {self.stream_id}")

    def OnWebSocketOpen(self, client, uri):
        print(f"Daydream: WebSocket client connected: {client}")
        self.ws_clients.add(client)

    def OnWebSocketClose(self, client):
        self.ws_clients.discard(client)

    def OnWebSocketReceiveText(self, client, data):
        pass

    def OnTimerPulse(self):
        if self.state != "STREAMING" or not self.ws_clients:
            return
        stream_source = self._stream_source
        web_server = self._web_server
        if not stream_source or not web_server:
            return
        try:
            jpeg_data = stream_source.saveByteArray('.jpg', quality=0.7)
            dead_clients = []
            for client in self.ws_clients:
                try:
                    web_server.webSocketSendBinary(client, jpeg_data)
                except:
                    dead_clients.append(client)
            for client in dead_clients:
                self.ws_clients.discard(client)
        except:
            pass

    def OnHTTPRequest(self, request, response, server_type='frame'):
        self.http.handle(request, response, server_type)

    def _scheduleParamsUpdate(self, par_name):
        self._pending_changes.add(par_name)
        self._emit('params_update_scheduled', {'param': par_name, 'pending': list(self._pending_changes)})
        if self._params_update_scheduled:
            return
        self._params_update_scheduled = True
        run(f"op('{self.ownerComp.path}').ext.Daydream._doParamsUpdate()", delayMilliSeconds=100)

    def _sanitize_params_for_emit(self, params):
        sanitized = dict(params)
        if 'ip_adapter_style_image_url' in sanitized:
            val = sanitized['ip_adapter_style_image_url']
            sanitized['has_style_image'] = bool(val)
            if val and val.startswith('data:'):
                sanitized['ip_adapter_style_image_url'] = '<data_url_omitted>'
        return sanitized

    def _doParamsUpdate(self):
        self._params_update_scheduled = False
        if self.state != "STREAMING" or not self.stream_id:
            return
        if not self._pending_changes:
            return
        changed = self._pending_changes.copy()
        self._pending_changes.clear()
        params = self.params.build_changed_params(changed)
        if not params:
            return
        stream_id = self.stream_id
        model_id = self.model_id
        sanitized = self._sanitize_params_for_emit(params)
        print(f"Daydream: Updating params (changed: {changed}): {sanitized}")
        self._emit('params_update_sent', {'changed': list(changed), 'params': sanitized})
        api = self.api
        owner_path = self.ownerComp.path
        def update_async():
            error = None
            try:
                api.update_stream(stream_id, model_id=model_id, **params)
            except Exception as e:
                error = str(e)
                print(f"Daydream Warning: Update failed. {e}")
            run(f"op('{owner_path}').ext.Daydream._onParamsUpdateResult({repr(error)})", delayFrames=1)
        self._executor.submit(update_async)

    def _onParamsUpdateResult(self, error):
        payload = {'success': error is None}
        if error:
            payload['error'] = error
        self._emit('params_update_result', payload)
        if error:
            self._emit('error', {'error': error, 'context': 'params_update'})

    def UpdateStatusText(self, text):
        text_op = self.ownerComp.op('text_overlay')
        if text_op:
            text_op.par.text = f"Daydream\n{text}"

    def OnParameterChange(self, par):
        print(f"Daydream: Parameter changed: {par.name} = {par.eval()}")
        hot_params = [
            'Prompt', 'Negprompt', 'Seed', 'Guidance', 'Delta',
            'Depth', 'Canny', 'Tile', 'Hed', 'Openpose', 'Color',
            'Ipadapter', 'Ipadapterscale', 'Styleimage',
        ]
        is_stepschedule = par.name.lower().startswith('stepschedule') and par.name.lower().endswith('step')
        if par.name == "Login":
            self.Login()
        elif par.name == "Resetparameters":
            self.ResetParameters()
        elif par.name == "Active":
            if par.eval():
                self.Start()
                self.params.update_cold_states(True)
            else:
                self.Stop()
        elif par.name == "Model":
            self.params.update_controlnet_states()
            self.params.update_ipadapter_states()
        elif par.name in hot_params or is_stepschedule:
            if par.name == 'Styleimage':
                self.params.invalidate_style_cache()
            if self.state == "STREAMING" and self.stream_id:
                self._scheduleParamsUpdate(par.name)

    def _get_relay_html(self):
        if self._relay_html_cache is None:
            self._relay_html_cache = RELAY_HTML_TEMPLATE.replace('{{SDP_PORT}}', str(self.sdp_port)).encode('utf-8')
        return self._relay_html_cache

    def Message(self, msg):
        print(f"Daydream Message: {msg}")


RELAY_HTML_TEMPLATE = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Daydream Relay</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #000; width: 512px; height: 512px; overflow: hidden; }
        #output-video { width: 512px; height: 512px; object-fit: cover; display: block; }
        #input-canvas { display: none; }
        #aurora {
            position: absolute;
            inset: 0;
            z-index: 100;
            pointer-events: none;
            transition: opacity 0.3s ease-out;
        }
        #aurora.hidden { opacity: 0; }
        #status {
            position: absolute;
            inset: 0;
            z-index: 101;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            pointer-events: none;
            transition: opacity 0.3s ease-out;
        }
        #status.hidden { opacity: 0; }
        #status-text {
            color: rgba(255, 255, 255, 0.9);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-size: 26px;
            font-weight: 500;
            text-align: center;
            text-shadow: 0 2px 6px rgba(0, 0, 0, 0.5);
        }
    </style>
</head>
<body>
    <video id="output-video" autoplay playsinline muted></video>
    <canvas id="input-canvas" width="512" height="512"></canvas>
    <canvas id="aurora" width="512" height="512"></canvas>
    <div id="status"><div id="status-text">Connecting...</div></div>
    <script>
        const ORIGIN = window.location.origin;
        const SDP_PORT = {{SDP_PORT}};
        const SDP_ORIGIN = window.location.protocol + '//' + window.location.hostname + ':' + SDP_PORT;
        const WS_URL = ORIGIN.replace('http', 'ws') + '/ws';
        const WHIP_PROXY = SDP_ORIGIN + '/whip';
        const WHEP_PROXY = SDP_ORIGIN + '/whep';

        const canvas = document.getElementById('input-canvas');
        const outputVideo = document.getElementById('output-video');
        const auroraCanvas = document.getElementById('aurora');
        const statusEl = document.getElementById('status');
        const statusText = document.getElementById('status-text');

        const useBitmapRenderer = !!canvas.getContext('bitmaprenderer');
        const ctx = useBitmapRenderer ? canvas.getContext('bitmaprenderer') : canvas.getContext('2d');
        const ctx2d = useBitmapRenderer ? null : ctx;

        let whipPC = null, whepPC = null;
        let ws = null;
        let latestFrame = null;
        let pendingDecode = null;
        let videoStarted = false;
        let whipStarted = false;
        let canvasStream = null;
        let videoTrack = null;
        let auroraWorker = null;

        const workerCode = `
            let canvas, ctx;
            let t = Math.random() * 100;
            let running = true;
            const DT = 0.016;
            
            const blobs = [
                { cx: 256, cy: 256, rx: 120, ry: 80, sx: 0.7, sy: 0.5, baseR: 200, hue: 260, phase: 0 },
                { cx: 256, cy: 256, rx: 100, ry: 120, sx: 0.5, sy: 0.8, baseR: 180, hue: 320, phase: 2 },
                { cx: 256, cy: 256, rx: 140, ry: 100, sx: 0.6, sy: 0.4, baseR: 160, hue: 220, phase: 4 },
                { cx: 256, cy: 256, rx: 80, ry: 140, sx: 0.4, sy: 0.6, baseR: 140, hue: 290, phase: 1 },
                { cx: 256, cy: 256, rx: 60, ry: 60, sx: 1.2, sy: 0.9, baseR: 80, hue: 200, phase: 3 },
                { cx: 256, cy: 256, rx: 50, ry: 70, sx: 0.9, sy: 1.1, baseR: 70, hue: 340, phase: 5 }
            ];
            
            function drawFrame() {
                if (!running || !ctx) return;
                const start = performance.now();
                t += DT;
                
                ctx.fillStyle = 'rgba(0, 0, 0, 0.08)';
                ctx.fillRect(0, 0, 512, 512);
                
                for (const blob of blobs) {
                    const x = blob.cx + Math.sin(t * blob.sx + blob.phase) * blob.rx;
                    const y = blob.cy + Math.cos(t * blob.sy + blob.phase * 0.7) * blob.ry;
                    const r = blob.baseR + Math.sin(t * 2 + blob.phase) * 25;
                    const hue = (blob.hue + t * 12) % 360;
                    
                    const gradient = ctx.createRadialGradient(x, y, 0, x, y, r);
                    gradient.addColorStop(0, 'hsla(' + hue + ', 75%, 60%, 0.18)');
                    gradient.addColorStop(0.15, 'hsla(' + hue + ', 72%, 57%, 0.14)');
                    gradient.addColorStop(0.3, 'hsla(' + hue + ', 70%, 54%, 0.10)');
                    gradient.addColorStop(0.5, 'hsla(' + hue + ', 67%, 50%, 0.06)');
                    gradient.addColorStop(0.7, 'hsla(' + hue + ', 63%, 46%, 0.03)');
                    gradient.addColorStop(0.85, 'hsla(' + hue + ', 58%, 43%, 0.01)');
                    gradient.addColorStop(1, 'hsla(' + hue + ', 55%, 40%, 0)');
                    
                    ctx.fillStyle = gradient;
                    ctx.fillRect(0, 0, 512, 512);
                }
                
                const elapsed = performance.now() - start;
                setTimeout(drawFrame, Math.max(0, 16 - elapsed));
            }
            
            self.onmessage = (e) => {
                if (e.data.type === 'init') {
                    canvas = e.data.canvas;
                    ctx = canvas.getContext('2d');
                    ctx.fillStyle = '#000';
                    ctx.fillRect(0, 0, 512, 512);
                    drawFrame();
                } else if (e.data.type === 'stop') {
                    running = false;
                }
            };
        `;

        function startAuroraWorker() {
            const offscreen = auroraCanvas.transferControlToOffscreen();
            const blob = new Blob([workerCode], { type: 'application/javascript' });
            auroraWorker = new Worker(URL.createObjectURL(blob));
            auroraWorker.postMessage({ type: 'init', canvas: offscreen }, [offscreen]);
            console.log('[Relay] Aurora worker started');
        }

        function stopAuroraWorker() {
            if (auroraWorker) {
                auroraWorker.postMessage({ type: 'stop' });
                auroraWorker.terminate();
                auroraWorker = null;
            }
        }

        function log(msg) {
            console.log('[Relay]', msg);
            statusText.textContent = msg;
        }

        function hideStatus() {
            auroraCanvas.classList.add('hidden');
            statusEl.classList.add('hidden');
            setTimeout(stopAuroraWorker, 300);
        }

        function preferH264(sdp) {
            const lines = sdp.split('\\r\\n');
            let videoMLineIndex = -1;
            let h264Payloads = [];
            for (let i = 0; i < lines.length; i++) {
                if (lines[i].startsWith('m=video')) videoMLineIndex = i;
                const match = lines[i].match(/a=rtpmap:(\\d+)\\s+H264/i);
                if (match) h264Payloads.push(match[1]);
            }
            if (videoMLineIndex >= 0 && h264Payloads.length > 0) {
                const parts = lines[videoMLineIndex].split(' ');
                const payloads = parts.slice(3);
                const sorted = [...h264Payloads, ...payloads.filter(p => !h264Payloads.includes(p))];
                parts.splice(3, payloads.length, ...sorted);
                lines[videoMLineIndex] = parts.join(' ');
            }
            return lines.join('\\r\\n');
        }

        function decodeLoop() {
            if (!latestFrame || pendingDecode) return;
            const frame = latestFrame;
            latestFrame = null;
            pendingDecode = createImageBitmap(new Blob([frame], { type: 'image/jpeg' }))
                .then(bitmap => {
                    if (useBitmapRenderer) {
                        ctx.transferFromImageBitmap(bitmap);
                    } else {
                        ctx2d.drawImage(bitmap, 0, 0, 512, 512);
                        bitmap.close();
                    }
                })
                .catch(() => {})
                .finally(() => {
                    pendingDecode = null;
                    if (latestFrame) decodeLoop();
                });
        }

        function connectWebSocket() {
            ws = new WebSocket(WS_URL);
            ws.binaryType = 'arraybuffer';
            ws.onopen = () => console.log('[Relay] WebSocket connected');
            ws.onmessage = (e) => {
                if (e.data instanceof ArrayBuffer) {
                    latestFrame = e.data;
                    decodeLoop();
                }
            };
            ws.onclose = () => {
                console.log('[Relay] WebSocket closed, reconnecting...');
                setTimeout(connectWebSocket, 1000);
            };
        }

        function warmupWebRTC() {
            console.log('[Relay] Warming up WebRTC...');
            canvasStream = canvas.captureStream(30);
            videoTrack = canvasStream.getVideoTracks()[0];
            whipPC = new RTCPeerConnection({
                iceServers: [
                    { urls: 'stun:stun.l.google.com:19302' },
                    { urls: 'stun:stun1.l.google.com:19302' }
                ]
            });
            whipPC.addTrack(videoTrack, canvasStream);
            console.log('[Relay] WebRTC warmed up');
        }

        async function pollStatus() {
            try {
                const res = await fetch(ORIGIN + '/status');
                if (!res.ok) { setTimeout(pollStatus, 100); return; }
                const data = await res.json();
                if (data.state === 'STREAMING' && data.whip_url && !whipStarted) {
                    whipStarted = true;
                    console.log('[Relay] Stream ready, starting WHIP');
                    startWHIP();
                } else if (!whipStarted) {
                    setTimeout(pollStatus, 100);
                }
            } catch {
                setTimeout(pollStatus, 100);
            }
        }

        async function startWHIP() {
            log('Connecting to server...');
            try {
                if (!videoTrack) {
                    canvasStream = canvas.captureStream(30);
                    videoTrack = canvasStream.getVideoTracks()[0];
                }
                if (!videoTrack) throw new Error('No video track from canvas');

                if (!whipPC || whipPC.signalingState === 'closed') {
                    whipPC = new RTCPeerConnection({
                        iceServers: [
                            { urls: 'stun:stun.l.google.com:19302' },
                            { urls: 'stun:stun1.l.google.com:19302' }
                        ]
                    });
                    whipPC.addTrack(videoTrack, canvasStream);
                }

                whipPC.oniceconnectionstatechange = () => {
                    console.log('[Relay] WHIP ICE:', whipPC.iceConnectionState);
                    if (whipPC.iceConnectionState === 'connected') log('Connected, waiting for AI...');
                    else if (whipPC.iceConnectionState === 'failed') log('Connection failed');
                };

                const offer = await whipPC.createOffer();
                const h264Sdp = preferH264(offer.sdp);
                await whipPC.setLocalDescription({ type: 'offer', sdp: h264Sdp });

                await new Promise(r => {
                    if (whipPC.iceGatheringState === 'complete') r();
                    else {
                        whipPC.onicegatheringstatechange = () => { if (whipPC.iceGatheringState === 'complete') r(); };
                        setTimeout(r, 2000);
                    }
                });

                console.log('[Relay] Sending WHIP offer via proxy');
                const response = await fetch(WHIP_PROXY, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/sdp' },
                    body: whipPC.localDescription.sdp
                });

                if (response.status === 202) {
                    const { id } = await response.json();
                    pollWhipResult(id);
                    return;
                }
                if (!response.ok) throw new Error('WHIP proxy error: ' + response.status);

                const answerSdp = await response.text();
                console.log('[Relay] Got WHIP answer');
                await whipPC.setRemoteDescription({ type: 'answer', sdp: answerSdp });
                startWHEP();
            } catch (e) {
                console.error('[Relay] WHIP error:', e);
                log('Connection error');
            }
        }

        async function pollWhipResult(id) {
            try {
                const response = await fetch(SDP_ORIGIN + '/whip/result/' + id);
                if (response.status === 202) { setTimeout(() => pollWhipResult(id), 100); return; }
                if (!response.ok) throw new Error('WHIP proxy error: ' + response.status);
                const answerSdp = await response.text();
                console.log('[Relay] Got WHIP answer');
                await whipPC.setRemoteDescription({ type: 'answer', sdp: answerSdp });
                startWHEP();
            } catch (e) {
                console.error('[Relay] WHIP poll error:', e);
                log('Connection error');
            }
        }

        let whepRetries = 0;

        async function startWHEP() {
            log('Waiting for AI stream...');
            try {
                if (whepPC) { try { whepPC.close(); } catch {} }
                whepPC = new RTCPeerConnection({
                    iceServers: [
                        { urls: 'stun:stun.l.google.com:19302' },
                        { urls: 'stun:stun1.l.google.com:19302' }
                    ]
                });

                whepPC.ontrack = (e) => {
                    console.log('[Relay] WHEP track:', e.track.kind);
                    if (e.track.kind === 'video') {
                        outputVideo.srcObject = e.streams[0] || new MediaStream([e.track]);
                        if (!videoStarted) log('Starting stream...');
                    }
                };

                whepPC.addTransceiver('video', { direction: 'recvonly' });
                whepPC.addTransceiver('audio', { direction: 'recvonly' });

                const offer = await whepPC.createOffer();
                await whepPC.setLocalDescription(offer);

                await new Promise(r => {
                    if (whepPC.iceGatheringState === 'complete') r();
                    else {
                        whepPC.onicegatheringstatechange = () => { if (whepPC.iceGatheringState === 'complete') r(); };
                        setTimeout(r, 2000);
                    }
                });

                const response = await fetch(WHEP_PROXY, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/sdp' },
                    body: whepPC.localDescription.sdp
                });

                if (response.status === 202) {
                    const { id } = await response.json();
                    pollWhepResult(id);
                    return;
                }
                if (!response.ok) {
                    if (whepRetries < 30) { whepRetries++; setTimeout(startWHEP, 500); return; }
                    throw new Error('WHEP failed');
                }

                const answerSdp = await response.text();
                await whepPC.setRemoteDescription({ type: 'answer', sdp: answerSdp });
                whepRetries = 0;
            } catch (e) {
                console.error('[Relay] WHEP error:', e);
                if (whepRetries < 30) { whepRetries++; setTimeout(startWHEP, 500); }
            }
        }

        async function pollWhepResult(id) {
            try {
                const response = await fetch(SDP_ORIGIN + '/whep/result/' + id);
                if (response.status === 202) { setTimeout(() => pollWhepResult(id), 100); return; }
                if (!response.ok) {
                    if (whepRetries < 30) { whepRetries++; setTimeout(startWHEP, 500); }
                    return;
                }
                const answerSdp = await response.text();
                await whepPC.setRemoteDescription({ type: 'answer', sdp: answerSdp });
                whepRetries = 0;
            } catch (e) {
                console.error('[Relay] WHEP poll error:', e);
                if (whepRetries < 30) { whepRetries++; setTimeout(startWHEP, 500); }
            }
        }

        function init() {
            log('Starting...');
            if (ctx2d) {
                ctx2d.fillStyle = '#000';
                ctx2d.fillRect(0, 0, 512, 512);
            }

            startAuroraWorker();

            outputVideo.onplaying = () => {
                if (!videoStarted) {
                    videoStarted = true;
                    console.log('[Relay] Video playing');
                    hideStatus();
                }
            };

            connectWebSocket();
            setTimeout(warmupWebRTC, 100);
            pollStatus();
        }

        init();
    </script>
</body>
</html>'''
