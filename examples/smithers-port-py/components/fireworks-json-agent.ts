// Minimal Fireworks JSON-output agent.
//
// Open-weights models on Fireworks (GLM, Kimi, DeepSeek) don't reliably
// honor the AI SDK's structured-output / function-calling protocol,
// which makes smithers' OpenAIAgent with nativeStructuredOutput:false
// brittle — the model emits reasoning preambles and free-form text, and
// smithers can't parse the response back to a typed row.
//
// This agent calls Fireworks' OpenAI-compatible /chat/completions
// endpoint directly with `response_format: { type: "json_object" }`,
// adds an explicit "JSON only" instruction to the prompt, and
// post-processes the response to strip leading reasoning preambles
// (matching common reasoning-model prefixes like "**Thought:**",
// "<think>...</think>", "Let me think...", etc.) before parsing.
//
// Returns the shape smithers' engine expects:
//   { text: string, _output: ParsedJson, usage: { inputTokens, outputTokens, ... } }
//
// Smithers' engine extracts `_output` first when present (see
// node_modules/@smithers-orchestrator/engine/src/engine.js line 3272-3279).

type GenerateArgs = {
  prompt?: string;
  messages?: { role: string; content: string }[];
  outputSchema?: any;
  abortSignal?: AbortSignal;
  timeout?: number;
};

type GenerateResult = {
  text: string;
  _output?: unknown;
  usage: {
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
    inputTokenDetails?: Record<string, number>;
    outputTokenDetails?: Record<string, number>;
  };
  finishReason?: string;
  response?: { modelId: string };
};


function stripReasoningPreamble(text: string): string {
  let s = text.trim();
  // Remove fenced code-block markers if the whole response is wrapped.
  if (s.startsWith("```")) {
    s = s.replace(/^```(?:json)?\s*\n?/i, "").replace(/\n?```\s*$/, "");
  }
  // Remove <think>...</think> blocks (DeepSeek, GLM reasoning syntax).
  s = s.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
  // Some models emit unclosed <think>... — drop everything up to the
  // first '{' if a stray <think> opener appears.
  if (/<think>/i.test(s)) {
    const brace = s.indexOf("{");
    if (brace >= 0) s = s.slice(brace);
  }
  // Strip leading "**Thought:**" / "1. Analyze the Request:" preambles.
  const firstBrace = s.indexOf("{");
  if (firstBrace > 0) {
    const preamble = s.slice(0, firstBrace);
    // Only strip the preamble if it doesn't contain quotes (which would
    // indicate it's actually part of the JSON body, e.g., a leading
    // string value). Should never happen for our schema (rooted on {})
    // but defensive.
    if (!/["']/.test(preamble)) {
      s = s.slice(firstBrace);
    }
  }
  // Trim trailing non-JSON (model sometimes adds explanation after the
  // closing brace).
  const lastBrace = s.lastIndexOf("}");
  if (lastBrace >= 0 && lastBrace < s.length - 1) {
    s = s.slice(0, lastBrace + 1);
  }
  return s.trim();
}


function flattenPrompt(args: GenerateArgs): { role: string; content: string }[] {
  if (args.messages && args.messages.length > 0) return args.messages;
  return [{ role: "user", content: args.prompt ?? "" }];
}


export class FireworksJsonAgent {
  readonly id: string;
  readonly model: string;
  private apiKey: string;
  private baseURL: string;

  constructor(opts: {
    model: string;
    apiKey: string;
    baseURL: string;
    id?: string;
  }) {
    this.model = opts.model;
    this.apiKey = opts.apiKey;
    this.baseURL = opts.baseURL.replace(/\/+$/, "");
    this.id = opts.id ?? `fireworks:${opts.model.split("/").pop()}`;
  }

  async generate(args: GenerateArgs = {}): Promise<GenerateResult> {
    const messages = flattenPrompt(args);

    // Augment the system/user prompt with an explicit JSON instruction.
    // We don't include the actual schema (the prompt already carries
    // a JSON shape description) — just force the response shape.
    const augmented = [
      {
        role: "system",
        content:
          "You produce strictly valid JSON responses. Output ONLY a single JSON " +
          "object that matches the schema described in the user message. Do not " +
          "include any reasoning, thinking tags, commentary, code fences, or text " +
          "outside the JSON object. The first character of your response must be '{' " +
          "and the last must be '}'.",
      },
      ...messages,
    ];

    const body: any = {
      model: this.model,
      messages: augmented,
      // Cap to keep cost predictable on retries. The translate response
      // is the largest expected output; 8k tokens of JSON is plenty.
      max_tokens: 8192,
      temperature: 0.2,
      response_format: { type: "json_object" },
    };

    const res = await fetch(`${this.baseURL}/chat/completions`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: args.abortSignal,
    });

    if (!res.ok) {
      const errBody = await res.text();
      throw new Error(`Fireworks ${this.model} ${res.status}: ${errBody.slice(0, 500)}`);
    }

    const data = await res.json();
    const text: string = data.choices?.[0]?.message?.content ?? "";
    const finishReason: string = data.choices?.[0]?.finish_reason ?? "stop";
    const usage = data.usage ?? {};
    const inputTokens = Number(usage.prompt_tokens ?? 0);
    const outputTokens = Number(usage.completion_tokens ?? 0);

    let parsed: unknown = undefined;
    const cleaned = stripReasoningPreamble(text);
    if (cleaned) {
      try {
        parsed = JSON.parse(cleaned);
      } catch {
        // Leave parsed undefined — smithers will fail schema validation
        // and retry (capped via Task retries={2}).
      }
    }

    return {
      text: cleaned,
      _output: parsed,
      usage: {
        inputTokens,
        outputTokens,
        totalTokens: inputTokens + outputTokens,
      },
      finishReason,
      response: { modelId: this.model },
    };
  }
}
