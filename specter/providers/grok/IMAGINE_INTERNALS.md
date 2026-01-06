# Grok Imagine Internals (Reverse Engineered)

Date: 2026-01-04

## Communication

- Uses **WebSocket** at `wss://grok.com/ws/imagine/listen` (not REST API)
- Messages use `conversation.item.create` type
- Can't intercept with Playwright's `page.route()` - only listens, not modifies
- Can monkey-patch `WebSocket.prototype.send` via init script to modify messages

## Request Structure

```json
{
  "type": "conversation.item.create",
  "timestamp": 1767550356749,
  "item": {
    "type": "message",
    "content": [{
      "requestId": "uuid",
      "text": "prompt here",
      "type": "input_text",
      "properties": {
        "section_count": 0,
        "is_kids_mode": false,
        "enable_nsfw": true,
        "skip_upsampler": false,
        "is_initial": false,
        "aspect_ratio": "2:3"
      }
    }]
  }
}
```

## Properties

| Property | Type | Notes |
|----------|------|-------|
| `section_count` | int | Always 0. Pagination section, NOT image count. Server ignores changes. |
| `is_kids_mode` | bool | Safe mode toggle |
| `enable_nsfw` | bool | Allows NSFW content (still gets moderated) |
| `skip_upsampler` | bool | Skip image upscaling |
| `is_initial` | bool | First generation in session |
| `aspect_ratio` | string | "2:3", "3:2", "1:1", "9:16", "16:9" |

## Image Count

- **Server-controlled** - not exposed as client parameter
- Likely tied to subscription tier:
  - Free: 4 images
  - SuperGrok: multiplier 1x
  - SuperGrokPro: multiplier 2x
- Chat API has `imageGenerationCount` but Imagine doesn't expose it

## Subscription Tiers

```javascript
// From JS bundle
SuperGrok: SubscriptionTierGrokPro
SuperGrokPro: SubscriptionTierSuperGrokPro

// Multiplier function
ef = e => e === SuperGrok ? 1 : 2 * (e === SuperGrokPro)
```

## Client Layout

- Masonry grid with `columnCount:2, maxColumnCount:5`
- Column count calculated from viewport width
- Display only - doesn't affect generation count

## localStorage Keys

| Key | Purpose |
|-----|---------|
| `useImagineModeStore` | Mode (image/video), aspect ratio, video length |
| `useImagineModelOverrideStore` | Model overrides (empty by default) |
| `imagine-mute-preference` | Sound preference |
| `visited-imagine2` | Tracking flag |

## JS Bundles (as of 2026-01-04)

- `32ef71dc445ef311.js` - Main imagine logic, `fetchImageGen` at ~978837
- `c03e82e10c1f6e50.js` - Masonry layout, section rendering

## Stack Trace (Generation Request)

```
WebSocket.prototype.send
  └─ send @ 32ef71dc445ef311.js:957403
      └─ fetch @ 32ef71dc445ef311.js:957512
          └─ fetchImageGen @ 32ef71dc445ef311.js:978837
              └─ @ c03e82e10c1f6e50.js:172522
```

## What We Can Control

✓ Aspect ratio (via localStorage + WS properties)
✓ Video mode/length (via localStorage)
✓ NSFW toggle (via WS properties)
✗ Image count (server-controlled)
✗ Model selection (not exposed in Imagine, only Chat)

## Moderation

- Even with `enable_nsfw: true`, images get moderated
- Moderated images appear blurred in DOM
- Blurred images have smaller base64 size (~50KB vs ~150KB+ for full)
- Detection: check `naturalWidth > 400` AND base64 length > 130000
