"""The Studio's ComfyUI graphs, in ComfyUI's API format ({node id: {class_type, inputs}}).

Each builder follows ComfyUI's own template for the model (the blueprints in ComfyUI's
blueprints/ folder, and the node packs' example workflows), with the Studio's files and sizes.
An input that comes from another node is [node id, output index].
"""

# The files, as ComfyUI lists them (SwarmUI's Models folder; see README "Studio").
KREA_UNET = "krea2_turbo_fp8_scaled.safetensors"
KREA_CLIP = "qwen3vl_4b.safetensors"
KREA_VAE = "QwenImage/qwen_image_vae.safetensors"
KREA_IDENTITY = "krea2_identity_edit_v2.safetensors"
H3_UNET = "minimax_h3_fl2va_pruned_w6a8.safetensors"
H3_CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
H3_VAE = "minimax_h3_video_vae_int8_convrot.safetensors"
H3_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
H3_TURBO = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
H3_UPSCALER = "minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors"
SEEDVR2_UNET = "seedvr2_3b_int8_convrot.safetensors"
SEEDVR2_VAE = "seedvr2_ema_vae_fp16.safetensors"

# Where each file lives under SwarmUI's Models folder (for the setup check).
MODEL_FILES = {
    KREA_UNET: "diffusion_models", KREA_CLIP: "text_encoders", KREA_VAE: "VAE",
    KREA_IDENTITY: "Lora", H3_UNET: "diffusion_models", H3_CLIP: "text_encoders",
    H3_VAE: "VAE", H3_AUDIO_VAE: "VAE", H3_TURBO: "Lora", H3_UPSCALER: "latent_upscale_models",
    SEEDVR2_UNET: "diffusion_models", SEEDVR2_VAE: "VAE",
}

# Which files each step needs.
NEEDS = {
    "still": [KREA_UNET, KREA_CLIP, KREA_VAE, KREA_IDENTITY],
    "looks": [KREA_UNET, KREA_CLIP, KREA_VAE, KREA_IDENTITY],
    "preview": [H3_UNET, H3_CLIP, H3_VAE, H3_AUDIO_VAE, H3_TURBO],
    "final": [H3_UNET, H3_CLIP, H3_VAE, H3_AUDIO_VAE, H3_TURBO, H3_UPSCALER],
    "upscale": [SEEDVR2_UNET, SEEDVR2_VAE],
}

# Sizes per aspect: the still and the final at H3's canvas (768 short edge, 768x1344 at most),
# the preview at ~0.3 MP. All multiples of 32.
SIZES = {
    "16:9": {"final": (1344, 768), "preview": (736, 416), "upscale": (1920, 1080)},
    "9:16": {"final": (768, 1344), "preview": (416, 736), "upscale": (1080, 1920)},
    "1:1": {"final": (1024, 1024), "preview": (544, 544), "upscale": (1440, 1440)},
}

# The refine pass's noise schedule: the node pack's example ("standard") or a lighter one that
# keeps more of the preview's look and motion ("gentle").
REFINE_SIGMAS = {"standard": "0.9035, 0.6316, 0.3158, 0.0", "gentle": "0.6316, 0.3158, 0.0"}

FPS = 24


def frames(seconds):
    """H3's frame grid: 17k+5 frames at 24 fps (124 = ~5 s)."""
    n = max(5, round(seconds * FPS))
    while n % 17 != 5:
        n += 1
    return n


class Graph:
    def __init__(self):
        self.nodes = {}

    def add(self, class_type, **inputs):
        nid = str(len(self.nodes) + 1)
        self.nodes[nid] = {"class_type": class_type, "inputs": inputs}
        return nid


def _save_video(g, images, audio, prefix, crf=16):
    video = g.add("CreateVideo", images=images, fps=float(FPS), **({"audio": audio} if audio else {}))
    g.add("SaveVideo", video=[video, 0], filename_prefix=prefix, **{
        "format": "mp4", "format.codec": "h264", "format.codec.encoding": "re-encode",
        "format.codec.encoding.crf": float(crf)})


# The Studio's own LoRAs: never offered as a style.
OWN_LORAS = {KREA_IDENTITY, H3_TURBO}


def still(prompts, refs, size, seed, prefix, steps=8, ref_boost=2.0, loras=()):
    """Krea 2 Turbo. With references ((uploaded image name, box or None), one per character; a
    box [x1, y1, x2, y2] crops the person out of a bigger picture): the Krea 2
    Identity Edit V2 LoRA, one pass per person, each with its own prompt; the first restages
    person 1, each further pass keeps the picture so far (aligned) and brings in the next
    person (beside). `loras`: [(file, strength)] the project adds (styles), under the identity one."""
    g = Graph()
    w, h = size
    unet = g.add("UNETLoader", unet_name=KREA_UNET, weight_dtype="default")
    for name, strength in loras:
        unet = g.add("LoraLoaderModelOnly", model=[unet, 0], lora_name=name, strength_model=float(strength))
    clip = g.add("CLIPLoader", clip_name=KREA_CLIP, type="krea2", device="default")
    vae = g.add("VAELoader", vae_name=KREA_VAE)
    target = g.add("EmptySD3LatentImage", width=w, height=h, batch_size=1)
    if not refs:
        pos = g.add("CLIPTextEncode", clip=[clip, 0], text=prompts[0])
        neg = g.add("ConditioningZeroOut", conditioning=[pos, 0])
        sample = g.add("KSampler", model=[unet, 0], positive=[pos, 0], negative=[neg, 0],
                       latent_image=[target, 0], seed=seed, steps=steps, cfg=1.0,
                       sampler_name="euler", scheduler="simple", denoise=1.0)
        image = g.add("VAEDecode", samples=[sample, 0], vae=[vae, 0])
        g.add("SaveImage", images=[image, 0], filename_prefix=prefix)
        return g.nodes
    model = g.add("LoraLoaderModelOnly", model=[unet, 0], lora_name=KREA_IDENTITY, strength_model=1.0)
    scene = None  # the picture so far, after the first person
    for i, ref in enumerate(refs):
        name, box = ref if isinstance(ref, tuple) else (ref, None)
        person = g.add("LoadImage", image=name)
        if box:
            x1, y1, x2, y2 = box
            person = g.add("ImageCrop", image=[person, 0], width=x2 - x1, height=y2 - y1, x=x1, y=y1)
        person_latent = g.add("VAEEncode", pixels=[person, 0], vae=[vae, 0])
        if scene is None:
            patch = dict(source_latent=[person_latent, 0], source_image=[person, 0],
                         ref_boost=ref_boost, ref_position="beside (right of target)")
            images = dict(image=[person, 0])
        else:
            scene_latent = g.add("VAEEncode", pixels=[scene, 0], vae=[vae, 0])
            patch = dict(source_latent=[scene_latent, 0], source_image=[scene, 0], ref_boost_a=1.0,
                         ref_position_a="aligned (trained)", source_latent_b=[person_latent, 0],
                         source_image_b=[person, 0], ref_boost=ref_boost,
                         ref_position="beside (right of target)")
            images = dict(image=[scene, 0], image_b=[person, 0])
        patched = g.add("Krea2EditModelPatch", model=[model, 0], vae=[vae, 0],
                        target_latent=[target, 0], fit_mode="fit", **patch)
        pos = g.add("Krea2EditGroundedEncode", clip=[clip, 0], prompt=prompts[min(i, len(prompts) - 1)], grounding_px=768,
                    system_prompt="", **images)
        neg = g.add("Krea2EditGroundedEncode", clip=[clip, 0], prompt="", grounding_px=768,
                    system_prompt="", **images)
        sample = g.add("KSampler", model=[patched, 0], positive=[pos, 0], negative=[neg, 0],
                       latent_image=[target, 0], seed=seed + i, steps=steps, cfg=1.0,
                       sampler_name="euler", scheduler="simple", denoise=1.0)
        scene = g.add("VAEDecode", samples=[sample, 0], vae=[vae, 0])
    g.add("SaveImage", images=[scene, 0], filename_prefix=prefix)
    return g.nodes


def _h3_base(g, prompt, first_frame, size, length, seed, steps):
    """MiniMax H3 from a first frame, as in ComfyUI's "Image to Video (MiniMax H3)" with the
    4-step turbo LoRA. Returns (model, clip, vae, audio_vae, image, sampled)."""
    unet = g.add("UNETLoader", unet_name=H3_UNET, weight_dtype="default")
    lora = g.add("LoraLoaderModelOnly", model=[unet, 0], lora_name=H3_TURBO, strength_model=1.0)
    model = g.add("MiniMaxH3SigmaShift", model=[lora, 0], shift_video=12.0, shift_audio=3.0)
    clip = g.add("CLIPLoader", clip_name=H3_CLIP, type="minimax", device="default")
    vae = g.add("VAELoader", vae_name=H3_VAE)
    audio_vae = g.add("VAELoader", vae_name=H3_AUDIO_VAE)
    image = g.add("LoadImage", image=first_frame)
    w, h = size
    cond = g.add("MiniMaxH3ImageToVideo", clip=[clip, 0], vae=[vae, 0], prompt=prompt, width=w,
                 height=h, length=length, first_frame=[image, 0])
    noise = g.add("RandomNoise", noise_seed=seed)
    sampler = g.add("KSamplerSelect", sampler_name="euler")
    sigmas = g.add("BasicScheduler", model=[model, 0], scheduler="simple", steps=steps, denoise=1.0)
    guider = g.add("BasicGuider", model=[model, 0], conditioning=[cond, 0])
    sampled = g.add("SamplerCustomAdvanced", noise=[noise, 0], guider=[guider, 0],
                    sampler=[sampler, 0], sigmas=[sigmas, 0], latent_image=[cond, 1])
    return model, clip, vae, audio_vae, image, sampled, noise, sampler


def preview(prompt, first_frame, size, seconds, seed, prefix, steps=4):
    """The low-resolution pass, to judge the motion."""
    g = Graph()
    _, _, vae, audio_vae, _, sampled, _, _ = _h3_base(g, prompt, first_frame, size, frames(seconds), seed, steps)
    images = g.add("VAEDecode", samples=[sampled, 0], vae=[vae, 0])
    audio = g.add("VAEDecodeAudio", samples=[sampled, 0], vae=[audio_vae, 0])
    _save_video(g, [images, 0], [audio, 0], prefix, crf=20)
    return g.nodes


def final(prompt, first_frame, preview_size, size, seconds, seed, prefix, steps=4, refine="standard"):
    """The approved preview again (same seed, so the same motion), its video latent upscaled by
    the learned H3 upscaler, then a short refine pass at full size: the node pack's example
    workflow ("minimax_h3_i2v Latent Upscaler")."""
    g = Graph()
    length = frames(seconds)
    model, clip, vae, audio_vae, image, sampled, noise, sampler = _h3_base(
        g, prompt, first_frame, preview_size, length, seed, steps)
    w, h = size
    av = g.add("LTXVSeparateAVLatent", av_latent=[sampled, 0])
    up = g.add("MinimaxH3LatentUpscaler3D", latent=[av, 0], model_name=H3_UPSCALER, align=32,
               keep_proportion=False, device="cuda", precision="bf16", offload_after_upscale=True,
               **{"mode": "target dimensions", "mode.width": w, "mode.height": h})
    joined = g.add("LTXVConcatAVLatent", video_latent=[up, 0], audio_latent=[av, 1])
    cond = g.add("MiniMaxH3ImageToVideo", clip=[clip, 0], vae=[vae, 0], prompt=prompt, width=w,
                 height=h, length=length, first_frame=[image, 0])
    guider = g.add("BasicGuider", model=[model, 0], conditioning=[cond, 0])
    sigmas = g.add("ManualSigmas", sigmas=REFINE_SIGMAS.get(refine, REFINE_SIGMAS["standard"]))
    refined = g.add("SamplerCustomAdvanced", noise=[noise, 0], guider=[guider, 0],
                    sampler=[sampler, 0], sigmas=[sigmas, 0], latent_image=[joined, 0])
    images = g.add("VAEDecode", samples=[refined, 0], vae=[vae, 0])
    audio = g.add("VAEDecodeAudio", samples=[refined, 0], vae=[audio_vae, 0])
    _save_video(g, [images, 0], [audio, 0], prefix)
    return g.nodes


# Frames per upscale piece. SeedVR2 holds several full copies of the clip at 1080p in RAM
# (~25 MB a frame each): a 15 s clip in one go reached 61 GB and was killed on a 64 GB box.
# 185 frames (~7.7 s) peaked at ~45 GB on the box; a 15 s clip is two pieces, one join.
UPSCALE_PIECE = 185

# How the upscale looks (a project setting; the first is the default):
#   clean:   Lanczos only: nothing invented, as sharp as the final (finish in Resolve). Looked
#            best in a side-by-side test: SeedVR2's invented skin texture shows even when mixed.
#   soft:    SeedVR2, chunks crossfaded, mixed half and half with a plain Lanczos upscale; it
#            invents less skin texture and doesn't shift from chunk to chunk.
#   seedvr2: SeedVR2 alone, the most invented detail.
UPSCALE_MODES = ("clean", "soft", "seedvr2")
SOFT_MIX = 0.5           # the share of the plain upscale in "soft"
CHUNK_OVERLAP = 4        # latent frames shared (and crossfaded) between SeedVR2's chunks


def upscale(video, size, seed, prefix, start=0, length=None, mode="soft"):
    """SeedVR2 3B, as in ComfyUI's "Upscale Video (SeedVR2 3B Int8)", in temporal chunks so
    a long clip fits a 16 GB card, or a plain upscale (see UPSCALE_MODES). Keeps the clip's
    audio and frame rate; with `length`, only those frames from `start`, and no audio
    (join() puts the pieces together)."""
    g = Graph()
    w, h = size
    src = g.add("LoadVideo", file=video)
    parts = g.add("GetVideoComponents", video=[src, 0])
    frames_in = [parts, 0]
    if length:
        frames_in = [g.add("ImageFromBatch", image=[parts, 0], batch_index=start, length=length), 0]
    resized = g.add("ResizeImageMaskNode", input=frames_in, scale_method="lanczos", **{
        "resize_type": "scale dimensions", "resize_type.width": w, "resize_type.height": h,
        "resize_type.crop": "center"})
    audio = None if length else [parts, 1]
    if mode == "clean":
        _save_video(g, [resized, 0], audio, prefix)
        return g.nodes
    pre = g.add("SeedVR2Preprocess", resized_images=[resized, 0])
    unet = g.add("UNETLoader", unet_name=SEEDVR2_UNET, weight_dtype="default")
    vae = g.add("VAELoader", vae_name=SEEDVR2_VAE)
    latent = g.add("VAEEncodeTiled", pixels=[pre, 0], vae=[vae, 0], tile_size=512, overlap=128,
                   temporal_size=64, temporal_overlap=8)
    overlap = 0 if mode == "seedvr2" else CHUNK_OVERLAP
    chunks = g.add("SeedVR2TemporalChunk", latent=[latent, 0], temporal_overlap=overlap, chunking_mode="auto")
    cond = g.add("SeedVR2Conditioning", model=[unet, 0], vae_conditioning=[chunks, 0])
    sample = g.add("KSampler", model=[unet, 0], positive=[cond, 0], negative=[cond, 1],
                   latent_image=[chunks, 0], seed=seed, steps=1, cfg=1.0, sampler_name="euler",
                   scheduler="simple", denoise=1.0)
    merged = g.add("SeedVR2TemporalMerge", latents=[sample, 0], temporal_overlap=[chunks, 1])
    decoded = g.add("VAEDecodeTiled", samples=[merged, 0], vae=[vae, 0], tile_size=512, overlap=128,
                    temporal_size=64, temporal_overlap=8)
    post = g.add("SeedVR2PostProcessing", images=[decoded, 0], original_resized_images=[resized, 0],
                 color_correction_method="lab")
    out = [post, 0]
    if mode == "soft":
        out = [g.add("ImageBlend", image1=[post, 0], image2=[resized, 0], blend_factor=SOFT_MIX, blend_mode="normal"), 0]
    _save_video(g, out, audio, prefix)
    return g.nodes


def join(pieces, audio_from, prefix):
    """The upscaled pieces (uploaded videos, in order) as one clip, with the sound of
    `audio_from` (the clip they came from)."""
    g = Graph()
    images = None
    for piece in pieces:
        part = g.add("GetVideoComponents", video=[g.add("LoadVideo", file=piece), 0])
        images = [part, 0] if images is None else [g.add("ImageBatch", image1=images, image2=[part, 0]), 0]
    sound = g.add("GetVideoComponents", video=[g.add("LoadVideo", file=audio_from), 0])
    _save_video(g, images, [sound, 1], prefix)
    return g.nodes
