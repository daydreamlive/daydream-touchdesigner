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

## Requirements

- TouchDesigner 2023+
- Daydream account ([daydream.live](https://daydream.live))
