Drop rnx2rtkp.exe here.

The pipeline looks for the RTKLIB solver automatically. The easiest option is to
put rnx2rtkp.exe (from RTKLIB-EX v2.5.1) directly in THIS folder — then
ppk_pipeline.py finds it with no setup and no source editing.

Download RTKLIB-EX v2.5.1:
  https://github.com/rtklibexplorer/RTKLIB/releases

Other ways to supply the solver (any one works):
  - add its folder to your PATH, or
  - set the RNX2RTKP environment variable to the full path of rnx2rtkp.exe, or
  - pass --rnx2rtkp "C:\path\to\rnx2rtkp.exe" on the command line.

The .exe itself is intentionally NOT committed to git (see .gitignore) — each
machine supplies its own copy of the matching RTKLIB version.
