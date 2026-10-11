# Data movement policy

The normative instruction list is [`policy.json`](../src/movbench/policy.json), identified by `zen-move-v1`. The benchmark targets Zen 5 by default and permits a common Zen 4/5 subset, rather than every instruction whose spelling starts with MOV or VPERM. Policy changes require a version change and generate a different measurement cohort.

Pure MOV loads/stores are supplemented by operations that route existing bits: lane/byte permutations, shuffles, broadcasts, unpacking, insertion/extraction, gathers/scatters, mask-register moves, and blends/compression/expansion. VPSHUFB's masked zeroing and MOVZX/MOVSX extension are deliberate exceptions to literal bit copying. Saturating packs, arithmetic shifts, floating-point conversions, and multiply/accumulate instructions are not permitted. In particular, VPMULTISHIFTQB is excluded even though it is in VBMI: its bit-shift semantics exceed this policy.

## Closed instruction body

Submissions contain an Intel-syntax body with one instruction per line. Blank lines and `#` comments are permitted; labels, directives, macro expressions, include files, symbol references, semicolon-separated statements, and byte injection are not. GCC's assembler emits the body; Capstone then decodes every byte. A source-level spelling check never substitutes for binary validation.

The harness appends exactly one `C3` RET, outside the contestant's body. There are no allowed control-flow instructions inside the body. Fixed problem sizes support compile-time unrolling. Supporting fault-driven MOV-only control flow, as in the original MOVfuscator, is a possible future track with a separate ABI and validation policy.

## Register and memory rules

| Register | Buffer | Mutability |
|---|---|---|
| rdi | fixed-size input | read-only |
| rsi | fixed-size output | read/write |
| rdx | `tables.bin` | read-only |
| rcx | declared scratch | read/write |

All four base registers must be preserved. RAX and R8–R11 and their subregisters, XMM/YMM/ZMM0–31, and K0–7 are writable. Callee-saved registers, RSP, control registers, and segment registers are unavailable as destinations. Every memory operand must use a buffer base; its scalar index can be RAX or R8–R11, and gather/scatter may use a vector index. Scaled addressing is permitted. Segment overrides, negative literal displacements, RIP-relative addressing, and absolute memory operands are rejected.

The data table contains bytes, not relocation-bearing pointers. Kernels must fully write their outputs and behave correctly with arbitrary initial scratch contents and prior invocation histories. There is no allocation, table initialization, or input preprocessing inside a timed contestant kernel. Offline computation may create constant tables, which count toward the memory tier. Outputs may be reused for intermediate values within their fixed allocation.

## Enforcement and limitations

Code has a 1 MiB cap separate from the table/scratch tier. Instruction immediates are allowed, including loading register-resident constants; their bytes count toward the code cap. Data directives and using code memory as a lookup table are forbidden. The runner records table length, scratch size, source and machine-code hashes, instruction counts, and each used mnemonic. Input/table pages are read-only during verification. Buffers end at inaccessible guard pages, while unused leading padding contains checked canaries. Code is non-writable during execution. Failed workers, wrong outputs, illegal opcodes, timeouts, and budget violations receive zero points.

These checks are not a proof of full functional correctness or an adversarial memory sandbox. Input-dependent accesses require testing; a malicious large index can leave its allocation, and leading padding can be read without faulting. Native execution uses a disposable child process and resource limits but no syscall isolation. For submissions from others, use an isolated machine or VM. The dashboard serves scores; it has no code-upload or execution route.

## References

- [Original M/o/Vfuscator and its MOV-only addressing/control-flow model](https://github.com/xoreaxeaxeax/movfuscator)
- [Intel SDM: instruction definitions and ISA feature requirements](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)
- [Intel Optimization Manual, including VBMI's any-to-any VPERMB](https://cdrdv2-public.intel.com/814198/248966-Optimization-Reference-Manual-V1-049.pdf)
- [AMD: AVX-512 and the Zen 5 512-bit datapath](https://www.amd.com/en/blogs/2026/understanding-avx-512---validating-usage-on-amd-epyc-.html)
