// 12 supported languages + downloadable I/O starter ("예시 코드"). These are
// platform-provided SKELETONS (not solutions): how to read the test input, how to
// OPTIONALLY open an uploaded data.bin via file I/O, and how to write the answer.
// Authors do NOT hand-write 12 codes — every problem ships these automatically.
//
// Submission rules (shown to contestants):
//   · 소스 코드 <= 1MB · data.bin(선택) <= 10MB · 시간/메모리 2초 / 1024MB (언어 공통)
//   · 입력 = 표준입력(stdin), 출력 = 표준출력(stdout)
//   · data.bin 을 함께 업로드하면 실행 디렉터리의 "data.bin" 파일로 읽을 수 있음(파일 입출력)
// Mirrors judge/languages.py.

export const SRC_LIMIT = 1_000_000;      // source code byte cap (1 MB)
export const DATA_LIMIT = 10_000_000;    // data.bin byte cap (10 MB)
export const DATA_FILENAME = "data.bin";

// `enabled` mirrors judge/languages.py — only baked-into-the-grader languages are
// submittable; the rest are shown as "준비 중" so the UI never promises an unrunnable lang.
export type Lang = { id: string; label: string; filename: string; starter: string; enabled?: boolean };

export const LANGS: Lang[] = [
  { id: "cpp20", label: "C++20", filename: "main.cpp", enabled: true, starter:
`#include <bits/stdc++.h>
using namespace std;
int main(){
    ios::sync_with_stdio(false); cin.tie(nullptr);
    // 입력: 표준입력에서 읽기.  예) int n; cin >> n;

    // (선택) 업로드한 data.bin 을 파일로 읽기:
    // if (FILE* f = fopen("data.bin", "rb")) {
    //     // fread(buf, 1, size, f) ...
    //     fclose(f);
    // }

    // 출력: 표준출력에 쓰기.  예) cout << ans << "\\n";
    return 0;
}
` },
  { id: "c17", label: "C17", filename: "main.c", enabled: true, starter:
`#include <stdio.h>
int main(void){
    /* 입력: stdin 에서 읽기.  예) scanf("%d", &n); */

    /* (선택) data.bin 파일 읽기:
       FILE *f = fopen("data.bin", "rb");
       if (f) { /* fread(...) *\\/ fclose(f); }
    */

    /* 출력: stdout 에 쓰기.  예) printf("%d\\n", ans); */
    return 0;
}
` },
  { id: "python3", label: "Python 3", filename: "main.py", enabled: true, starter:
`import sys, os

def main():
    data = sys.stdin.buffer.read()          # 입력(표준입력)

    # (선택) 업로드한 data.bin 읽기
    if os.path.exists("data.bin"):
        with open("data.bin", "rb") as f:
            blob = f.read()

    # 출력: print(...) 또는 sys.stdout.write(...)

if __name__ == "__main__":
    main()
` },
  { id: "java21", label: "Java 21", filename: "Main.java", starter:
`import java.util.*;
import java.io.*;
import java.nio.file.*;
public class Main {
    public static void main(String[] args) throws IOException {
        BufferedReader br = new BufferedReader(new InputStreamReader(System.in)); // 입력

        // (선택) data.bin 읽기
        // if (Files.exists(Paths.get("data.bin"))) {
        //     byte[] blob = Files.readAllBytes(Paths.get("data.bin"));
        // }

        // 출력: System.out.print(...)
    }
}
` },
  { id: "csharp", label: "C#", filename: "Main.cs", starter:
`using System;
using System.IO;
class Program {
    static void Main() {
        string input = Console.In.ReadToEnd();          // 입력

        // (선택) data.bin 읽기
        // if (File.Exists("data.bin")) {
        //     byte[] blob = File.ReadAllBytes("data.bin");
        // }

        // 출력: Console.Write(...)
    }
}
` },
  { id: "kotlin", label: "Kotlin", filename: "Main.kt", starter:
`import java.io.*
fun main() {
    val input = System.\`in\`.bufferedReader().readText()   // 입력

    // (선택) data.bin 읽기
    // val f = File("data.bin")
    // if (f.exists()) { val blob = f.readBytes() }

    // 출력: print(...)
}
` },
  { id: "go", label: "Go", filename: "main.go", starter:
`package main
import ("bufio"; "os")
func main(){
    r := bufio.NewReader(os.Stdin)                 // 입력
    w := bufio.NewWriter(os.Stdout); defer w.Flush()
    _ = r

    // (선택) data.bin 읽기
    // if blob, err := os.ReadFile("data.bin"); err == nil { _ = blob }

    // 출력: w.WriteString(...)
}
` },
  { id: "rust", label: "Rust", filename: "main.rs", starter:
`use std::io::{self, Read, Write};
fn main(){
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();   // 입력

    // (선택) data.bin 읽기
    // if let Ok(blob) = std::fs::read("data.bin") { let _ = blob; }

    let out = io::stdout(); let mut o = out.lock();
    let _ = &mut o;                                     // 출력: write!(o, "...")
}
` },
  { id: "node", label: "JavaScript (Node)", filename: "main.js", starter:
`const fs = require("fs");
const input = fs.readFileSync(0, "utf8");            // 입력(표준입력)

// (선택) data.bin 읽기
// if (fs.existsSync("data.bin")) { const blob = fs.readFileSync("data.bin"); }

// 출력: process.stdout.write(...)
` },
  { id: "ruby", label: "Ruby", filename: "main.rb", starter:
`input = STDIN.read                                  # 입력

# (선택) data.bin 읽기
# blob = File.binread("data.bin") if File.exist?("data.bin")

# 출력: print ... / puts ...
` },
  { id: "swift", label: "Swift", filename: "main.swift", starter:
`import Foundation
let input = String(data: FileHandle.standardInput.readDataToEndOfFile(), encoding: .utf8) ?? ""  // 입력

// (선택) data.bin 읽기
// if let blob = try? Data(contentsOf: URL(fileURLWithPath: "data.bin")) { _ = blob }

// 출력: print(...)
` },
  { id: "php", label: "PHP", filename: "main.php", starter:
`<?php
$input = stream_get_contents(STDIN);                // 입력

// (선택) data.bin 읽기
// if (is_file("data.bin")) { $blob = file_get_contents("data.bin"); }

// 출력: echo ...;
` },
];

export const LANG_BY_ID: Record<string, Lang> = Object.fromEntries(LANGS.map((l) => [l.id, l]));

export function downloadText(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// human-readable byte size, e.g. 1_048_576 -> "1.0 MB"
export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1_000_000) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1_000_000).toFixed(2)} MB`;
}
