r"""Sprite Forge command line. Every command prints one JSON object on stdout.

  forge.py setup [--dry-run] [--all]           install the models for this GPU, unattended
  forge.py doctor                              detect hardware, check ComfyUI and model files
  forge.py profiles                            list profiles, styles and saved characters
  forge.py sprite NAME "prompt" [-p sprite] [--preset fast|standard|max] [--set size=256]
                                               text -> game-ready transparent PNG / sheet
      profiles: sprite, icon, pixel_sprite, character, turnaround, animation, tile, portrait, vfx
      sheets:   --character ranger [--character-description "..."]  --set action=walk --set directions=4
  forge.py character ID "description"          create a named character reference
  forge.py batch  jobs.json                    many assets, grouped by model
  forge.py check  FILE [--kind sprite]         measure a PNG: transparency, cut-off, shapes, seam, problems
  forge.py models [--install NAME ...]         what each profile needs / download catalog items
  forge.py init   PROJECT_DIR                  write a starter spriteforge.json into a game project
  forge.py connect [--claude] [--codex] [--project DIR] [--remove]   register the MCP server + skill
  forge.py bundle                              build dist/sprite-forge.mcpb (Claude Desktop extension)
  forge.py mcp                                 run the MCP server (used by the connectors)

Common options: --project DIR  --out DIR  --style "..."  --style-preset ID  --seed N  --force
Exit code: 0 ok, 2 finished but the result failed its check, 3 setup blocked/partial, 1 error.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _params(pairs):
    out = {}
    for pair in pairs or []:
        k, _, v = pair.partition("=")
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out or None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--project", help="game project folder (uses its spriteforge.json)")
        p.add_argument("--out", help="output folder")
        p.add_argument("--style", default="")
        p.add_argument("--style-preset", help="style preset id (forge.py profiles lists them)")
        p.add_argument("--seed", type=int)
        p.add_argument("--force", action="store_true")
        p.add_argument("--set", action="append", metavar="KEY=VALUE", help="override a profile parameter")

    sub.add_parser("doctor")
    p = sub.add_parser("setup"); p.add_argument("--dry-run", action="store_true"); p.add_argument("--skip-models", action="store_true")
    p.add_argument("--skip-nodes", action="store_true"); p.add_argument("--pinned", action="store_true"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("profiles"); p.add_argument("--project")
    p = sub.add_parser("sprite"); p.add_argument("name"); p.add_argument("prompt", nargs="?", default=None)
    p.add_argument("-p", "--profile", default="sprite"); p.add_argument("--preset", choices=["fast", "standard", "max"], default="standard")
    p.add_argument("--character"); p.add_argument("--character-description"); common(p)
    p = sub.add_parser("character"); p.add_argument("character"); p.add_argument("description")
    p.add_argument("--preset", choices=["fast", "standard", "max"], default="standard"); common(p)
    p = sub.add_parser("batch"); p.add_argument("jobs"); common(p)
    p = sub.add_parser("check"); p.add_argument("file"); p.add_argument("--kind", default="sprite")
    p = sub.add_parser("models"); p.add_argument("--project"); p.add_argument("--install", nargs="+")
    p = sub.add_parser("init"); p.add_argument("project_dir")
    p = sub.add_parser("connect"); p.add_argument("--claude", action="store_true"); p.add_argument("--codex", action="store_true")
    p.add_argument("--project"); p.add_argument("--remove", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("bundle"); p.add_argument("--out")
    sub.add_parser("mcp")
    a = ap.parse_args()

    # Image processing needs numpy/Pillow/scipy, which ComfyUI's Python has. When started
    # with a Python that lacks them (e.g. as a plugin via plain `python`), hand over.
    try:
        import numpy, PIL, scipy  # noqa: F401,E401
    except ImportError:
        from spriteforge.config import load_machine
        comfy_py = load_machine().get("comfy_python", "")
        if comfy_py and os.path.isfile(comfy_py) and os.path.normcase(comfy_py) != os.path.normcase(sys.executable):
            import subprocess
            sys.exit(subprocess.call([comfy_py, os.path.abspath(__file__), *sys.argv[1:]]))

    if a.cmd == "mcp":
        from spriteforge import mcp_server
        return mcp_server.serve()

    try:
        from spriteforge import comfy, config, connect, models, pipeline, profiles
        if a.cmd == "doctor":
            m = config.load_machine(refresh=True)
            up = comfy.is_up()
            rep = models.report()
            out = {"status": "ok", "machine": m, "comfyui_found": bool(m["comfy_root"]), "comfyui_running": up,
                   "guide_for_this_tier": rep["guide_for_this_tier"],
                   "profiles": [{k: p[k] for k in ("profile", "ready", "fits_this_gpu", "missing")} for p in rep["profiles"]]}
            if up:  # with ComfyUI running, also confirm every node type a profile uses is installed
                info = comfy.object_info()
                for entry in out["profiles"]:
                    p = profiles.resolve(entry["profile"])
                    types = set()
                    for rel in [x for x in (p.get("graph"), p.get("edit_graph")) if x]:
                        g = json.load(open(profiles.graph_path({**p, "graph": rel}), encoding="utf-8-sig"))
                        types |= {n["class_type"] for n in g.values()}
                    entry["missing_nodes"] = sorted(t for t in types if t not in info)
                    entry["ready"] = entry["ready"] and not entry["missing_nodes"]
        elif a.cmd == "setup":
            from spriteforge import setup as setup_mod
            out = setup_mod.setup(a.dry_run, a.skip_models, a.skip_nodes, a.pinned, a.all)
        elif a.cmd == "profiles":
            out = {"status": "ok", "profiles": profiles.list_profiles(a.project), "styles": profiles.list_styles(a.project),
                   "characters": pipeline.list_characters(a.project)}
        elif a.cmd == "sprite":
            out = pipeline.generate_sprite(a.profile, a.name, a.prompt, a.project, a.out, a.seed, a.style, _params(a.set),
                                           a.force, a.preset, a.style_preset, a.character, a.character_description)
        elif a.cmd == "character":
            out = pipeline.create_character(a.character, a.description, a.project, a.seed, a.preset, a.style_preset, a.style,
                                            _params(a.set), a.force)
        elif a.cmd == "batch":
            jobs = json.load(open(a.jobs, encoding="utf-8-sig"))
            results = pipeline.generate_batch(jobs, a.project, a.out, a.style, a.force)
            bad = [r["name"] for r in results if r.get("status") != "ok"]
            out = {"status": "ok" if not bad else "failed_check", "count": len(results), "not_ok": bad, "assets": results}
        elif a.cmd == "check":
            out = pipeline.check_file(a.file, a.kind)
            if out["problems"]:
                out["status"] = "failed_check"
        elif a.cmd == "models":
            out = {"status": "ok", "installed": models.install(a.install)} if a.install else {"status": "ok", **models.report(a.project)}
        elif a.cmd == "init":
            out = connect.init_project(a.project_dir)
        elif a.cmd == "bundle":
            out = connect.make_bundle(a.out)
        elif a.cmd == "connect":
            out = connect.connect(claude=a.claude, codex=a.codex, project=a.project, remove=a.remove, dry_run=a.dry_run)
    except Exception as e:
        print(json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}"}))
        sys.exit(1)

    print(json.dumps(out, indent=1, default=str))
    sys.exit(0 if out.get("status") == "ok" else 3 if out.get("status") in ("blocked", "partial") else 2)


if __name__ == "__main__":
    main()
