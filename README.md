# ComfyUI-Specter

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-Custom_Node-blue)](https://github.com/comfyanonymous/ComfyUI)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)

**Use ChatGPT, Grok, Gemini, and Google Flow in ComfyUI.** No API keys, no extra costs. Just your existing accounts (even free tiers work).

*Specter is a stealthy browser phantom that automates web interfaces in the background. Headless and invisible.*

![demo](demo.jpg)
![demo2](demo2.png)

https://github.com/user-attachments/assets/ffbe5846-24ae-4c7c-a393-4b504e196287

## How It Works

```mermaid
flowchart LR
    A[ComfyUI Workflow] --> B[Specter Node]
    B --> C[Chrome Browser]
    C --> D{Provider}
    D --> E[ChatGPT]
    D --> F[Grok]
    D --> G[Google Gemini]
    D --> K[Google Flow]
    E --> H[Generated Content]
    F --> H
    G --> H
    K --> H
    H --> A

    C -.-> I[(Session Storage)]
    I -.-> C
```

## Why Specter?

Already paying for ChatGPT Plus/Pro, X Premium, or Google AI Pro/Ultra? Use those features in ComfyUI without extra API costs. All providers also offer free tiers that work with this extension.

| | API | Your Existing Subscription |
|---|---|---|
| **ChatGPT Image** | ~$0.05/image | Included |
| **GPT-5.2** | $0.88-7/1M tokens | Included |
| **Grok Image** | $0.01/image | Included |
| **Grok Video** | No API | Included |
| **Gemini** | $0.075-0.30/1M tokens | Included |
| **Google Flow Images** | No API | Free |
| **Google Flow Video** | No API | 50 credits free |

## Installation

### Windows

1. Clone to your ComfyUI `custom_nodes` folder:
   ```cmd
   cd ComfyUI\custom_nodes
   git clone https://github.com/lars-hagen/ComfyUI-Specter.git
   ```

2. Install dependencies:
   ```cmd
   cd ComfyUI-Specter
   pip install -r requirements.txt
   patchright install chrome
   ```

3. Restart ComfyUI

### macOS / Linux

1. Clone to your ComfyUI `custom_nodes` folder:
   ```bash
   cd ComfyUI/custom_nodes
   git clone https://github.com/lars-hagen/ComfyUI-Specter.git
   ```

2. Install dependencies:
   ```bash
   cd ComfyUI-Specter
   pip install -r requirements.txt
   patchright install chrome
   ```

3. Restart ComfyUI

### Authentication

After installation, authenticate with your accounts:

**Option 1: Embedded Browser (Recommended)**
- **Automatic:** Run any Specter node - a login popup appears if needed
- **Manual:** Go to Settings > Specter > Providers > Sign In

https://github.com/user-attachments/assets/81329d1c-42d0-48c0-9137-a19ed5b8ba41

**Option 2: Cookie Import**

If the embedded browser doesn't work (VPN, network restrictions, etc.), import cookies from your regular browser:

1. Install [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) (Chrome/Edge) or [Firefox version](https://addons.mozilla.org/en-US/firefox/addon/get-cookies-txt-locally/)
2. Go to [chatgpt.com](https://chatgpt.com), [grok.com](https://grok.com), or [gemini.google.com](https://gemini.google.com) and log in
3. Click the extension icon and export cookies (JSON or Netscape TXT format)
4. In ComfyUI: Settings > Specter > Providers > click the import button > paste or drop the file

Sessions save automatically for future use.

## Nodes

### OpenAI (ChatGPT)

| Node | Description |
|------|-------------|
| **OpenAI ChatGPT** | Text chat with GPT models (supports image input) |
| **OpenAI GPT Image 1** | Image generation and editing with GPT Image 1.5 |

### xAI (Grok)

| Node | Description |
|------|-------------|
| **xAI Grok** | Text chat with Grok models (supports image input) |
| **xAI Grok Imagine** | Text-to-image generation |
| **xAI Grok Imagine Edit** | Image-to-image editing |
| **xAI Grok Imagine Video** | Text-to-video generation |
| **xAI Grok Imagine Video I2V** | Image-to-video generation |
| **xAI Grok Video Combine** | Combine two videos sequentially |

### Google (Gemini)

| Node | Description |
|------|-------------|
| **Google Gemini** | Multimodal chat (images, audio, video, files) |
| **Google Nano Banana** | Image generation with Gemini 1.5 Flash |
| **Google Nano Banana Pro** | Image generation with Gemini 3.0 models |

### Google Flow

| Node | Description |
|------|-------------|
| **Google Flow Text to Image** | Text-to-image with Imagen/Nano Banana models |
| **Google Flow Text to Video** | Text-to-video with Veo models (3.x has audio) |
| **Google Flow Image Edit** | Edit images with text instructions |
| **Google Flow Image to Video** | Animate with first/last frame control |
| **Google Flow Reference to Video** | Generate video from reference images |

### Tools

| Node | Description |
|------|-------------|
| **Specter Prompt Enhancer** | Enhance prompts using any chat model |
| **Google Prompt Enhancer** | Enhance prompts for Google's image models |
| **Specter Image Describer** | Generate descriptions from images |
| **Load Files** | Load files from disk for Gemini input |

## Example Workflows

### ChatGPT

#### ChatGPT Chat
![chatgpt_chat](example_workflows/chatgpt_chat.jpg)
[Download workflow](example_workflows/chatgpt_chat.json)

#### ChatGPT Text to Image
![chatgpt_txt2img](example_workflows/chatgpt_txt2img.jpg)
[Download workflow](example_workflows/chatgpt_txt2img.json)

#### ChatGPT Image to Image
![chatgpt_img2img](example_workflows/chatgpt_img2img.jpg)
[Download workflow](example_workflows/chatgpt_img2img.json)

#### ChatGPT Prompt Enhancer
![chatgpt_prompt_enhancer](example_workflows/chatgpt_prompt_enhancer.jpg)
[Download workflow](example_workflows/chatgpt_prompt_enhancer.json)

#### ChatGPT Image Describer
![chatgpt_image_describer](example_workflows/chatgpt_image_describer.jpg)
[Download workflow](example_workflows/chatgpt_image_describer.json)

### Grok

#### Grok Chat
![grok_chat](example_workflows/grok_chat.jpg)
[Download workflow](example_workflows/grok_chat.json)

#### Grok Text to Image
![grok_txt2img](example_workflows/grok_txt2img.jpg)
[Download workflow](example_workflows/grok_txt2img.json)

#### Grok Image Edit
![grok_img_edit](example_workflows/grok_img_edit.jpg)
[Download workflow](example_workflows/grok_img_edit.json)

#### Grok Text to Video
![grok_txt2vid](example_workflows/grok_txt2vid.jpg)
[Download workflow](example_workflows/grok_txt2vid.json)

#### Grok Image to Video
![grok_img2vid](example_workflows/grok_img2vid.jpg)
[Download workflow](example_workflows/grok_img2vid.json)

### Gemini

#### Gemini Chat
![gemini_chat](example_workflows/gemini_chat.jpg)
[Download workflow](example_workflows/gemini_chat.json)

#### Gemini Text to Image
![gemini_txt2img](example_workflows/gemini_txt2img.jpg)
[Download workflow](example_workflows/gemini_txt2img.json)

### Google Flow

#### Flow Text to Image
![flow_txt2img](example_workflows/flow_txt2img.jpg)
[Download workflow](example_workflows/flow_txt2img.json)

#### Flow Text to Video
![flow_txt2vid](example_workflows/flow_txt2vid.jpg)
[Download workflow](example_workflows/flow_txt2vid.json)

#### Flow Image Edit
![flow_img_edit](example_workflows/flow_img_edit.jpg)
[Download workflow](example_workflows/flow_img_edit.json)

#### Flow Image to Video
![flow_i2v](example_workflows/flow_i2v.jpg)
[Download workflow](example_workflows/flow_i2v.json)

#### Flow Reference to Video
![flow_ref2v](example_workflows/flow_ref2v.jpg)
[Download workflow](example_workflows/flow_ref2v.json)

## Rate Limits

| | Free | Paid | Top Tier |
|---|---|---|---|
| **ChatGPT Image** | ~3/day | ~50/3hr (Plus) | Unlimited (Pro) |
| **ChatGPT Text** | Limited | ~80/3hr (Plus) | Unlimited (Pro) |
| **Grok Image** | ~20/day | 100/day (Premium) | 200/day (SuperGrok) |
| **Grok Video** | ~20/day | 100/day (Premium) | 200/day (SuperGrok) |
| **Gemini** | 50/day | Higher (AI Pro) | Highest (AI Ultra) |
| **Google Flow Images** | Free (daily limit) | Free | Free |
| **Google Flow Video** | 50 credits/mo | 1,000/mo (AI Pro) | 25,000/mo (AI Ultra) |

## Troubleshooting

- **"Missing system dependencies"** - Run `patchright install chrome` to install the browser
- **Session expired?** Go to Settings > Specter > Providers and click Sign In
- **Browser not closing?** Check for zombie Chrome processes
- **Login loop?** Delete session via Settings > Specter or remove `user_data/` folder
