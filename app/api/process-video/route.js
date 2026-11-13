// Preserve old clients while routing all processing through the owned queue.
import { POST as generate } from '../generate/route';

export async function POST(request) {
  return generate({
    json: async () => {
      const body = await request.json();
      return { videoUrl: body?.videoUrl ?? body?.url };
    },
  });
}
