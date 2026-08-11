"""STEP 6: Make ALL the audio for the video in one place.

Three kinds of audio, all from ElevenLabs:
  - one MUSIC bed for the whole video (looping underscore),
  - one AMBIENCE bed per location (looping room tone), reused across scenes in that place,
  - one VOICEOVER per scene, spoken in the narrator's fixed voice.

The next step (scene_clips) renders each scene's silent video sized to the voiceover made
here and lays that voiceover over it, so the picture length always matches the voice. The
audio functions themselves live in scene_clips (tts / make_ambient / make_music); this step
just drives them, stores the results, and prints the ElevenLabs cost on its own.

Reads:  output/analysis.json   (scenes, characters + voice_ids, per-scene narration + ambience)
Writes: output/vo_NN.mp3        (one voiceover per scene), output/amb_<place>.mp3 (per location),
        output/music.mp3, and writes each scene's voiceover file + length + character count and
        the ambience file back into analysis.json, plus the ElevenLabs cost under "costs".
Cost:   ElevenLabs speech $0.10 / 1,000 characters; ElevenLabs sound $0.002 / second.
"""
import os
import sys
import json
from dotenv import load_dotenv
import costs
# The audio generators already exist in scene_clips; reuse them so there's ONE implementation.
from scene_clips import OUT_DIR, slug, tts, make_ambient, make_music, audio_duration

load_dotenv()


def main():
    analysis_path = os.path.join(OUT_DIR, "analysis.json")
    if not os.path.exists(analysis_path):
        sys.exit("Missing output/analysis.json. Run scene_writer.py first.")
    with open(analysis_path) as f:
        data = json.load(f)

    script = data.get("script") or {}
    scenes = script.get("scenes", [])
    if not scenes:
        sys.exit("No scenes found. Run scene_writer.py first.")
    setting = script.get("setting", "")

    # ONE voice carries the whole video: the first non-anonymous character (the protagonist).
    lead_c = next((c for c in data.get("characters", []) if not c.get("anonymous")), None)
    lead_voice = (lead_c or {}).get("voice_id", "")

    # Cost totals count ONLY what is generated THIS run — reused files (already on disk from a
    # prior run) add nothing, so a repair/redo that reuses all the audio correctly shows $0.
    tts_chars = 0
    tts_seconds = 0.0
    ambient_seconds = 0.0

    # 1) MUSIC — one looping underscore for the whole video.
    music_path = os.path.join(OUT_DIR, "music.mp3")
    if os.path.exists(music_path) and os.path.getsize(music_path) > 0:
        print("  (reusing music bed music.mp3 — already on disk, $0)")
    else:
        msecs = make_music(music_path)
        ambient_seconds += msecs
        if msecs > 0:
            print(f"  music bed: {msecs:.0f} seconds x ${costs.ELEVEN_SFX_PER_SEC}/second "
                  f"= ${msecs * costs.ELEVEN_SFX_PER_SEC:.4f}  (ElevenLabs sound)")

    # 2) AMBIENCE — one looping bed per unique location, reused across its scenes.
    ambient_by_loc = {}
    for sc in scenes:
        loc_key = (sc.get("setting", setting) or setting).strip().lower()
        amb_file = ambient_by_loc.get(loc_key)
        if amb_file is None:
            amb_name = f"amb_{slug(loc_key)[:40] or 'room'}.mp3"
            amb_path = os.path.join(OUT_DIR, amb_name)
            if os.path.exists(amb_path) and os.path.getsize(amb_path) > 0:
                amb_file = amb_name
                print(f"  (reusing ambient bed {amb_name} — already on disk, $0)")
            else:
                # Use the scene's ambience description (party chatter, train rumble) so the bed
                # is characterful; fall back to the location name if none was written.
                amb_desc = (sc.get("ambience") or "").strip() or loc_key
                secs = make_ambient(amb_desc, amb_path)
                amb_file = amb_name if secs > 0 else ""
                ambient_seconds += secs
                if secs > 0:
                    print(f"  ambient bed {amb_name}: {secs:.0f} seconds x ${costs.ELEVEN_SFX_PER_SEC}/second "
                          f"= ${secs * costs.ELEVEN_SFX_PER_SEC:.4f}  (ElevenLabs sound)")
            ambient_by_loc[loc_key] = amb_file
        if amb_file:
            sc["ambient"] = amb_file

    # 3) VOICEOVER — one track per scene in the narrator's voice. The file + its length and
    # character count are stored on the scene so the video step can size and mux without
    # re-generating anything.
    voiceovers = 0
    for i, sc in enumerate(scenes, 1):
        vo_name = f"vo_{i:02d}.mp3"
        vo_path = os.path.join(OUT_DIR, vo_name)
        vo_text = (sc.get("narration") or "").strip()
        if not vo_text:
            sc["vo_file"], sc["vo_seconds"], sc["vo_chars"] = "", 0.0, 0
            print(f"  [{i}] no voiceover text — silent scene")
            continue
        if os.path.exists(vo_path) and os.path.getsize(vo_path) > 0:
            # Reuse a voiceover already on disk: measure its length, keep the stored char count.
            secs, chars = audio_duration(vo_path), sc.get("vo_chars", 0)
            print(f"  [{i}] (reusing voiceover {vo_name} — already on disk, $0)")
        else:
            secs, chars = tts(lead_voice, vo_text, vo_path)
            tts_seconds += secs
            tts_chars += chars
            if secs > 0:
                cost = chars / 1000 * costs.ELEVEN_TTS_PER_1K_CHARS
                print(f"  [{i}] voiceover {vo_name}: {secs:.1f} seconds, {chars} characters, "
                      f"${cost:.4f}  (ElevenLabs speech)")
        sc["vo_file"] = vo_name if secs > 0 else ""
        sc["vo_seconds"] = round(secs, 2)
        sc["vo_chars"] = chars
        if secs > 0:
            voiceovers += 1

    # Record the two ElevenLabs costs (speech + sound) under their own keys, and drop the old
    # combined "clips" entry an older run may have left behind.
    tts_cost = tts_chars / 1000 * costs.ELEVEN_TTS_PER_1K_CHARS
    sfx_cost = ambient_seconds * costs.ELEVEN_SFX_PER_SEC
    data.get("costs", {}).pop("clips", None)
    costs.record(data, "tts",
                 f"Voiceover - ElevenLabs speech ({tts_chars} characters, {tts_seconds:.0f} seconds)", tts_cost)
    costs.record(data, "sound",
                 f"Ambience + music - ElevenLabs sound ({ambient_seconds:.0f} seconds)", sfx_cost)

    with open(analysis_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nUpdated output/analysis.json with the audio "
          f"({voiceovers} voiceovers, {len({v for v in ambient_by_loc.values() if v})} ambience beds, music).")
    print("\n  Audio cost — each paid piece:")
    print(f"    Voiceover       (ElevenLabs speech):    {tts_chars} characters "
          f"({tts_seconds:.0f} seconds of audio) x $0.10 per 1000 characters = ${tts_cost:.4f}")
    print(f"    Ambience+music  (ElevenLabs sound):     {ambient_seconds:.0f} seconds "
          f"x ${costs.ELEVEN_SFX_PER_SEC}/second = ${sfx_cost:.4f}")
    print(f"    -> ElevenLabs total: {tts_seconds + ambient_seconds:.0f} seconds of audio "
          f"generated = ${tts_cost + sfx_cost:.4f}")
    costs.show("All audio (ElevenLabs voice + ambience + music)", tts_cost + sfx_cost)


if __name__ == "__main__":
    main()
