# Daydream TouchDesigner Plugin

Real-time AI image generation for TouchDesigner with Daydream

## Features

- **Real-time streaming** via WebRTC (WHIP/WHEP)
- **Multiple models**: SDXL Turbo, SD Turbo, Dreamshaper 8, Openjourney v4
- **ControlNet**: Depth, Canny, Tile, HED, OpenPose, Color
- **IP Adapter**: Style transfer with regular and FaceID modes

## Setup

1. Drop the component into your project
2. Connect a TOP to `stream_source` input
3. Click **Login** to authenticate with your Daydream account
4. Toggle **Active** to start streaming

## Parameters

| Parameter         | Description                                |
| ----------------- | ------------------------------------------ |
| Prompt            | Text description of desired output         |
| Negative Prompt   | What to avoid in generation                |
| Seed              | Randomization seed (-1 for random)         |
| Guidance          | How closely to follow the prompt           |
| Delta             | Strength of diffusion effect               |
| Steps             | Number of inference steps                  |
| ControlNet scales | Strength of each conditioning type         |
| IP Adapter        | Enable style transfer from reference image |

## Integration API

For developers building wrapper plugins or integrating Daydream as a submodule.

### Public Contract

The extension exposes `PUBLIC_CONTRACT` with introspectable metadata:

```python
PUBLIC_CONTRACT = {
    'extension_name': 'Daydream',
    'lifecycle_methods': ['Login', 'Start', 'Stop', 'ResetParameters'],
    'state_properties': ['state', 'Active', 'IsLoggedIn', 'ApiToken', 'stream_id', 'whip_url', 'whep_url'],
    'states': ['IDLE', 'CREATING', 'STREAMING', 'ERROR'],
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
```

### GetCapabilities

Query runtime capabilities for the current model:

```python
ext = op('/daydream').ext.Daydream
caps = ext.GetCapabilities()
# {
#     'backend': 'daydream',
#     'version': 'x.y.z',
#     'model': 'stabilityai/sdxl-turbo',
#     'supported_models': ['stabilityai/sdxl-turbo', ...],
#     'controlnets': ['depth', 'canny', 'tile'],
#     'ip_adapter_types': ['regular', 'faceid'],
# }
```

### Lifecycle Callbacks

Register a listener to receive lifecycle events without polling:

```python
def on_event(event, payload):
    # payload always includes: owner_path, state, stream_id
    if event == 'streaming_started':
        print(f"Stream ready: {payload['stream_id']}")
    elif event == 'state_changed':
        print(f"State: {payload['from']} -> {payload['to']}")
    elif event == 'error':
        print(f"Error in {payload.get('context')}: {payload['error']}")

ext = op('/daydream').ext.Daydream
ext.register_listener(on_event)
# ext.unregister_listener(on_event)
```

| Event                     | Payload                                         |
| ------------------------- | ----------------------------------------------- |
| `initialized`             | `logged_in`                                     |
| `login_started`           | `auth_port`                                     |
| `login_success`           | -                                               |
| `login_failed`            | `error`                                         |
| `stream_create_started`   | `model`                                         |
| `stream_created`          | `whip_url`, `model_id`                          |
| `stream_create_failed`    | `error`                                         |
| `streaming_started`       | `whip_url`, `whep_url`, `model_id`              |
| `streaming_stopped`       | `prev_stream_id`                                |
| `params_update_scheduled` | `param`, `pending`                              |
| `params_update_sent`      | `changed`, `params`                             |
| `params_update_result`    | `success`, `error` (if failed)                  |
| `state_changed`           | `from`, `to`, `reason`, `error` (if applicable) |
| `error`                   | `error`, `context`, `will_retry` (for WHIP)     |

## Requirements

- TouchDesigner 2023+
- Daydream account ([daydream.live](https://daydream.live))
