export async function onRequest(context) {
    const request = context.request;
    const url = new URL(request.url);
    
    // Handle CORS preflight requests
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 200,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Session-ID, X-Request-ID, X-Cancel-Previous",
          "Access-Control-Allow-Credentials": "true",
          "Access-Control-Max-Age": "86400",
        },
      });
    }
    
    // Direct API route handling only
    const directApiEndpoints = {
      '/api/chat': '/api/chat',
      '/api/react-search': '/api/react-search',
      '/api/react-search-streaming': '/api/react-search-streaming',
      '/api/related-questions': '/api/related-questions',
      '/api/health': '/api/health',
      '/api/models': '/api/models',
      '/api/upload': '/api/upload'
    };
    
    // Check if this is an API endpoint we need to forward
    let targetPath = null;
    
    // Check direct API routes
    for (const [directPath, backendPath] of Object.entries(directApiEndpoints)) {
      if (url.pathname.startsWith(directPath)) {
        targetPath = url.pathname;
        break;
      }
    }
    
    if (targetPath) {
      // Create backend API URL
      const backendUrl = new URL(targetPath, process.env.API_BASE_URL || "http://localhost:8000");
      
      // Forward query parameters
      url.searchParams.forEach((value, key) => {
        backendUrl.searchParams.append(key, value);
      });
      
      // Create a new request to forward to your API
      const apiRequest = new Request(backendUrl.toString(), {
        method: request.method,
        headers: request.headers,
        body: request.method !== "GET" ? request.body : undefined,
        redirect: 'follow',
      });
      
      try {
        // Forward the request to your API
        const apiResponse = await fetch(apiRequest);
        
        // Create a new response with CORS headers
        const responseHeaders = new Headers(apiResponse.headers);
        responseHeaders.set("Access-Control-Allow-Origin", "*");
        responseHeaders.set("Access-Control-Allow-Credentials", "true");
        
        // Set streaming headers for API chat endpoints
        if (url.pathname.includes('/api/chat') || url.pathname.includes('/api/react-search')) {
          responseHeaders.set("Content-Type", "text/event-stream");
          responseHeaders.set("Cache-Control", "no-cache");
          responseHeaders.set("Connection", "keep-alive");
          responseHeaders.set("X-Accel-Buffering", "no");
        }
        
        return new Response(apiResponse.body, {
          status: apiResponse.status,
          statusText: apiResponse.statusText,
          headers: responseHeaders,
        });
      } catch (error) {
        console.error(`Error forwarding request: ${error.message}`);
        return new Response(JSON.stringify({ error: error.message }), {
          status: 500,
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Session-ID, X-Request-ID, X-Cancel-Previous",
            "Access-Control-Allow-Credentials": "true",
          },
        });
      }
    }
    
    // For non-API routes, let Cloudflare Pages handle it
    const response = await context.next();
    const newResponse = new Response(response.body, response);
    newResponse.headers.set("Access-Control-Allow-Origin", "*");
    newResponse.headers.set("Access-Control-Allow-Credentials", "true");
    
    return newResponse;
  }