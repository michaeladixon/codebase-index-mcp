import { ApiClient } from "./api";
import { LogLevel } from "./helpers";

export async function run(): Promise<void> {
  const client = new ApiClient("https://api.example.com", {
    attempts: 3,
    backoffMs: 250,
  });
  const users = await client.getJson("users");
  console.log(LogLevel.Info, users);
}

run();
