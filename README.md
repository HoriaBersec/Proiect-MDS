# Proiect-MDS

AI Scene Agents — Blender Addon

A Blender addon that puts two AI agents in the sidebar, each backed by the Anthropic API with real tool use. You type what you want in plain language, the agent inspects the scene, decides what to call, and does it, no manual clicking through menus.

The two agents

Scene Manager — cleanup and organization

Handles deleting, renaming, grouping into collections, hiding, moving things around.

Tools: get_scene_info, delete_objects, rename_object, set_transform, move_to_collection, set_visibility.

The one thing worth calling out: this agent needs some judgment, not blind string matching. "Delete the rocks" should catch every rock-shaped object — including .001, .002 duplicates — but shouldn't nuke a RockWall mesh just because the word "rock" shows up in the name. That distinction is baked into the system prompt.

Material Artist — procedural materials with actual node graphs

Builds real Shader Editor node graphs instead of setting a flat color:


create_procedural_material — a single pattern (Noise for organic variation, Voronoi for cellular variation) driving a Color Ramp into Base Color, with optional roughness variation on the same pattern.
create_weathered_material — two full PBR layers (color + metallic + roughness each) blended by a noise mask. For anything with a "clean" layer and a "damaged/aged" layer on top: rust over metal, moss over stone, chipped paint over wood.


Both are idempotent: re-running on the same material clears out the nodes it created last time instead of piling up duplicates.

Architecture

Prompt
  └─> Anthropic Messages API (per-agent system prompt + tool schemas)
        └─> tool_use blocks ──> executed locally via bpy ──> tool_result
              └─> back to the model ... (loop, capped at 15 turns)
                    └─> final reply + full log in the Text Editor (AgentLog)

Each agent has its own system prompt and its own subset of tools. The model decides on its own what to call and in what order — it's told to always call get_scene_info first.

Install

Blender 5.0.0
Edit > Preferences > Add-ons > Install... → pick ai_scene_agents.py → enable it.
In the addon's preferences, paste your Anthropic API key.


Known limitations

The UI freezes while an agent is running (synchronous API call).
No mesh modeling agent — a primitive-based "Builder" agent was tried early on and cut, results were weak for the effort.
Materials don't do bump/normal maps yet, only color + metallic + roughness.
Needs an internet connection and a valid API key with credits.




Here is a small test scene I made, it's just some rocks in a 'desert' and I asked the material agent to assign 3 different rock materials randomly among the rocks in the scene:

![image alt](https://github.com/HoriaBersec/Proiect-MDS/blob/644fc818ea9ac67d54ed816848e6d7088127c6b5/screenshots/basic_scene_setup.jpg)

Now I won't show what the scene manager agent does, because all you could do in this scene with it would be to delete all the rocks(wow!) and I dont think that needs any showcasing.

I asked the agent to make the sand material more interesting and here is what i got:
![image alt](https://github.com/HoriaBersec/Proiect-MDS/blob/644fc818ea9ac67d54ed816848e6d7088127c6b5/screenshots/new_sand-variation.jpg)

here are the shader nodes it created:
![image alt]([https://github.com/HoriaBersec/Proiect-MDS/blob/644fc818ea9ac67d54ed816848e6d7088127c6b5/screenshots/sand-shader-nodes.jpg](https://github.com/HoriaBersec/Proiect-MDS/blob/7d4bccfebefdbd2ce2723b197cc6c372cdb7904a/screenshots/sand_shader_nodes.jpg))

After that I just told it to make the the sand material effect more pronuced and develop the stone materials the way it thinks it should:
![image alt](https://github.com/HoriaBersec/Proiect-MDS/blob/644fc818ea9ac67d54ed816848e6d7088127c6b5/screenshots/latest_sand-variation.jpg)
![image alt](https://github.com/HoriaBersec/Proiect-MDS/blob/644fc818ea9ac67d54ed816848e6d7088127c6b5/screenshots/rock_new_material.jpg)
