/**
 * Codex VS Code extension entry point (VS Code + Nervous-System scope
 * change; see docs/vscode-nervous-system-architecture.md).
 *
 * Two commands only, per the MVP scope (§9): "Codex: Index Repository"
 * (repository lifecycle) and "Codex: Explore Symbol Neighborhood"
 * (symbol lookup -> bounded neighborhood -> Webview). Speaks to Codex
 * exclusively through `CodexClient`'s HTTP calls to a local
 * `python -m codex.api` process this extension spawns and owns for the
 * lifetime of the window -- never imports Python code, never reads the
 * graph store's files directly (the "Critical Architectural Principle"
 * constraint, enforced here by having no code path capable of doing
 * either).
 */

import * as cp from "child_process";
import * as vscode from "vscode";
import { AskPanel } from "./askPanel";
import { CodexClient, IngestionJobStatus } from "./codexClient";
import { NeighborhoodPanel } from "./neighborhoodPanel";

let serverProcess: cp.ChildProcess | undefined;
let client: CodexClient | undefined;
let serverStarting: Promise<CodexClient> | undefined;

const LISTENING_PATTERN = /CODEX_API_LISTENING (\S+) (\d+)/;
const SERVER_START_TIMEOUT_MS = 15000;

function startServer(): Promise<CodexClient> {
  return new Promise((resolve, reject) => {
    const proc = cp.spawn("python3", ["-m", "codex.api", "--port", "0"], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    serverProcess = proc;
    let settled = false;
    let stderrBuffer = "";

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        proc.kill();
        reject(
          new Error(
            "timed out waiting for the Codex API server to start -- is `codex` installed for python3?"
          )
        );
      }
    }, SERVER_START_TIMEOUT_MS);

    proc.stdout?.on("data", (chunk: Buffer) => {
      if (settled) {
        return;
      }
      const match = LISTENING_PATTERN.exec(chunk.toString());
      if (match) {
        settled = true;
        clearTimeout(timer);
        resolve(new CodexClient(`http://${match[1]}:${match[2]}`));
      }
    });
    proc.stderr?.on("data", (chunk: Buffer) => {
      stderrBuffer += chunk.toString();
    });
    proc.on("exit", (code) => {
      serverProcess = undefined;
      client = undefined;
      serverStarting = undefined;
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(
          new Error(
            `Codex API server exited (code ${code}) before it started listening: ${stderrBuffer.trim()}`
          )
        );
      }
    });
    proc.on("error", (err) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(err);
      }
    });
  });
}

async function ensureServer(): Promise<CodexClient> {
  if (client) {
    return client;
  }
  if (!serverStarting) {
    serverStarting = startServer().then((c) => {
      client = c;
      return c;
    });
  }
  return serverStarting;
}

function currentRepository(): { repositoryId: string; localPath: string } | undefined {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    return undefined;
  }
  const folder = folders[0];
  return { repositoryId: folder.name, localPath: folder.uri.fsPath };
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("codex.askQuestion", async () => {
      const repo = currentRepository();
      if (!repo) {
        void vscode.window.showErrorMessage("Codex: open a folder first.");
        return;
      }
      try {
        const c = await ensureServer();
        await AskPanel.show(c, repo);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        void vscode.window.showErrorMessage(`Codex: could not start the Codex API server: ${message}`);
      }
    }),

    vscode.commands.registerCommand("codex.indexWorkspace", async () => {
      const repo = currentRepository();
      if (!repo) {
        void vscode.window.showErrorMessage("Codex: open a folder first.");
        return;
      }
      try {
        const c = await ensureServer();
        await vscode.window.withProgress(
          {
            location: vscode.ProgressLocation.Notification,
            title: `Codex: indexing ${repo.repositoryId}`,
            cancellable: false,
          },
          async (progress) => {
            const handle = await c.registerAndIndex(repo.repositoryId, repo.localPath);
            await c.waitForJob(handle.job_id, (status: IngestionJobStatus) => {
              progress.report({ message: status.phase });
            });
          }
        );
        const status = await c.getRepositoryStatus(repo.repositoryId);
        if (status.phase === "READY") {
          void vscode.window.showInformationMessage(`Codex: ${repo.repositoryId} is ready.`);
        } else {
          void vscode.window.showErrorMessage(
            `Codex: indexing ${repo.repositoryId} did not complete (phase=${status.phase}).`
          );
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        void vscode.window.showErrorMessage(`Codex: indexing failed: ${message}`);
      }
    }),

    vscode.commands.registerCommand("codex.exploreSymbol", async () => {
      const repo = currentRepository();
      if (!repo) {
        void vscode.window.showErrorMessage("Codex: open a folder first.");
        return;
      }
      const query = await vscode.window.showInputBox({
        prompt: "Codex: symbol, file, or name to explore",
        placeHolder: "e.g. a function, class, or method name",
      });
      if (!query) {
        return;
      }
      try {
        const c = await ensureServer();
        const lookup = await c.lookupSymbols(repo.repositoryId, query);
        if (lookup.nodes.length === 0) {
          void vscode.window.showWarningMessage(`Codex: no match for "${query}".`);
          return;
        }
        let chosenId: string;
        if (lookup.nodes.length === 1) {
          chosenId = lookup.nodes[0].id;
        } else {
          const pick = await vscode.window.showQuickPick(
            lookup.nodes.map((node) => ({
              label: node.name,
              description: `${node.node_type} — ${node.qualified_name}`,
              id: node.id,
            })),
            { placeHolder: `Codex: ${lookup.nodes.length} matches for "${query}"` }
          );
          if (!pick) {
            return;
          }
          chosenId = pick.id;
        }
        await NeighborhoodPanel.show(c, repo.repositoryId, chosenId);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        void vscode.window.showErrorMessage(`Codex: ${message}`);
      }
    })
  );
}

export function deactivate(): void {
  serverProcess?.kill();
  serverProcess = undefined;
  client = undefined;
  serverStarting = undefined;
}
