import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import matter from "gray-matter";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function getNotesDir(): string {
  const configPath = path.resolve(__dirname, "../../config.json");
  if (fs.existsSync(configPath)) {
    const config = JSON.parse(fs.readFileSync(configPath, "utf-8")) as {
      obsidian_inbox_dir?: string;
    };
    if (config.obsidian_inbox_dir) {
      return config.obsidian_inbox_dir;
    }
  }
  const envDir = process.env.MEMNON_NOTES_DIR;
  if (envDir) return envDir;
  throw new Error(
    "Cannot determine notes directory: config.json missing obsidian_inbox_dir and MEMNON_NOTES_DIR env var not set"
  );
}

// ---------------------------------------------------------------------------
// Note types
// ---------------------------------------------------------------------------

interface NoteFrontmatter {
  title?: string;
  created?: string;
  processed?: string;
  suggested_tags?: string[];
  status?: string;
  type?: string;
  summary?: string;
  action_items?: string[];
}

interface NoteRecord {
  filename: string;
  filepath: string;
  frontmatter: NoteFrontmatter;
  content: string; // full raw file content
  body: string;    // content after frontmatter
}

// ---------------------------------------------------------------------------
// Note loading
// ---------------------------------------------------------------------------

function loadNotes(notesDir: string): NoteRecord[] {
  if (!fs.existsSync(notesDir)) {
    return [];
  }

  const files = fs
    .readdirSync(notesDir)
    .filter((f) => f.endsWith(".md"))
    .sort(); // alphabetical / chronological (filenames start with date)

  return files.map((filename) => {
    const filepath = path.join(notesDir, filename);
    const raw = fs.readFileSync(filepath, "utf-8");
    const parsed = matter(raw);
    return {
      filename,
      filepath,
      frontmatter: parsed.data as NoteFrontmatter,
      content: raw,
      body: parsed.content,
    };
  });
}

// ---------------------------------------------------------------------------
// Tool helpers
// ---------------------------------------------------------------------------

function noteMetaSummary(note: NoteRecord) {
  const fm = note.frontmatter;
  return {
    filename: note.filename,
    title: fm.title ?? note.filename,
    created: fm.created ?? null,
    tags: fm.suggested_tags ?? [],
    summary: fm.summary ?? null,
  };
}

function parseISODate(s: string): Date {
  return new Date(s);
}

function extractActionItems(body: string): string[] {
  const lines = body.split("\n");
  return lines
    .filter((l) => /^\s*- \[ \]/.test(l))
    .map((l) => l.replace(/^\s*- \[ \]\s*/, "").trim())
    .filter((l) => l.length > 0 && l.toLowerCase() !== "none extracted");
}

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

const server = new Server(
  { name: "memnon-mcp", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// List tools
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "list_notes",
      description:
        "List recent voice notes with metadata (title, date, tags, summary). " +
        "Use this to browse what notes exist before diving into content.",
      inputSchema: {
        type: "object" as const,
        properties: {
          limit: {
            type: "number",
            description: "Maximum number of notes to return (default: 10)",
          },
          since: {
            type: "string",
            description:
              "ISO date string — only return notes created on or after this date",
          },
        },
      },
    },
    {
      name: "search_notes",
      description:
        "Full-text search across note content and titles. Returns matching notes with context.",
      inputSchema: {
        type: "object" as const,
        properties: {
          query: {
            type: "string",
            description: "Search query string (case-insensitive)",
          },
          tags: {
            type: "array",
            items: { type: "string" },
            description: "Optional list of tags to filter by (any match)",
          },
        },
        required: ["query"],
      },
    },
    {
      name: "get_note",
      description: "Get the full content of a specific note by filename.",
      inputSchema: {
        type: "object" as const,
        properties: {
          filename: {
            type: "string",
            description: "The filename of the note (e.g. '2026-05-13 150409 audio-recording.md')",
          },
        },
        required: ["filename"],
      },
    },
    {
      name: "get_action_items",
      description:
        "Return all unchecked action items (- [ ] …) across all notes, " +
        "each annotated with its source note title and date.",
      inputSchema: {
        type: "object" as const,
        properties: {},
      },
    },
  ],
}));

// Call tools
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const notesDir = getNotesDir();
  const notes = loadNotes(notesDir);

  const { name, arguments: args } = request.params;
  const a = (args ?? {}) as Record<string, unknown>;

  // -------------------------------------------------------------------------
  // list_notes
  // -------------------------------------------------------------------------
  if (name === "list_notes") {
    const limit = typeof a.limit === "number" ? a.limit : 10;
    const since = typeof a.since === "string" ? parseISODate(a.since) : null;

    let filtered = notes;
    if (since) {
      filtered = notes.filter((n) => {
        const created = n.frontmatter.created;
        return created ? parseISODate(created) >= since : false;
      });
    }

    // Return newest first
    const sorted = filtered.slice().reverse().slice(0, limit);
    const result = sorted.map(noteMetaSummary);

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(result, null, 2),
        },
      ],
    };
  }

  // -------------------------------------------------------------------------
  // search_notes
  // -------------------------------------------------------------------------
  if (name === "search_notes") {
    const query = typeof a.query === "string" ? a.query.toLowerCase() : "";
    const tags = Array.isArray(a.tags)
      ? (a.tags as string[]).map((t) => t.toLowerCase())
      : [];

    const results = notes
      .filter((note) => {
        // Tag filter (any match)
        if (tags.length > 0) {
          const noteTags = (note.frontmatter.suggested_tags ?? []).map((t) =>
            t.toLowerCase()
          );
          const titleLower = (note.frontmatter.title ?? "").toLowerCase();
          const hasTag = tags.some(
            (t) => noteTags.includes(t) || titleLower.includes(t)
          );
          if (!hasTag) return false;
        }

        // Text search
        const searchable = [
          note.filename,
          note.frontmatter.title ?? "",
          note.frontmatter.summary ?? "",
          note.body,
        ]
          .join(" ")
          .toLowerCase();

        return searchable.includes(query);
      })
      .reverse() // newest first
      .map((note) => {
        // Find lines containing the query for context
        const bodyLines = note.body.split("\n");
        const matchLines = bodyLines
          .filter((l) => l.toLowerCase().includes(query))
          .slice(0, 3)
          .map((l) => l.trim());

        return {
          ...noteMetaSummary(note),
          matches: matchLines,
        };
      });

    return {
      content: [
        {
          type: "text",
          text:
            results.length === 0
              ? `No notes found matching "${a.query}".`
              : JSON.stringify(results, null, 2),
        },
      ],
    };
  }

  // -------------------------------------------------------------------------
  // get_note
  // -------------------------------------------------------------------------
  if (name === "get_note") {
    const filename = typeof a.filename === "string" ? a.filename : "";
    const note = notes.find((n) => n.filename === filename);

    if (!note) {
      return {
        content: [
          {
            type: "text",
            text: `Note not found: ${filename}\n\nAvailable notes:\n${notes.map((n) => n.filename).join("\n")}`,
          },
        ],
        isError: true,
      };
    }

    return {
      content: [
        {
          type: "text",
          text: note.content,
        },
      ],
    };
  }

  // -------------------------------------------------------------------------
  // get_action_items
  // -------------------------------------------------------------------------
  if (name === "get_action_items") {
    const allItems: Array<{
      action_item: string;
      source_title: string;
      source_file: string;
      created: string | null;
    }> = [];

    for (const note of notes) {
      const items = extractActionItems(note.body);
      for (const item of items) {
        allItems.push({
          action_item: item,
          source_title: note.frontmatter.title ?? note.filename,
          source_file: note.filename,
          created: note.frontmatter.created ?? null,
        });
      }
    }

    // Newest first
    allItems.sort((a, b) => {
      if (!a.created && !b.created) return 0;
      if (!a.created) return 1;
      if (!b.created) return -1;
      return new Date(b.created).getTime() - new Date(a.created).getTime();
    });

    return {
      content: [
        {
          type: "text",
          text:
            allItems.length === 0
              ? "No unchecked action items found across all notes."
              : JSON.stringify(allItems, null, 2),
        },
      ],
    };
  }

  return {
    content: [{ type: "text", text: `Unknown tool: ${name}` }],
    isError: true,
  };
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // Log to stderr so it doesn't pollute the MCP stdio channel
  process.stderr.write("Memnon MCP server running\n");
}

main().catch((err) => {
  process.stderr.write(`Fatal: ${err}\n`);
  process.exit(1);
});
