package com.snapscan.offline;

/** Portable parts of RapidOCR 1.4.4's Apache-2.0 preprocessing/CTC contract.
 * Reimplemented for Java; no Python runtime, network calls or model changes.
 */
final class OcrMath {
    static final class Decoded {
        final String text;
        final double score;
        Decoded(String text, double score) { this.text = text; this.score = score; }
    }

    static Decoded decode(float[][] probabilities, String[] dictionary) {
        StringBuilder text = new StringBuilder();
        double sum = 0;
        int emitted = 0, previous = -1;
        for (float[] step : probabilities) {
            if (step.length != dictionary.length) throw new IllegalArgumentException("CTC dictionary/output mismatch");
            int best = 0;
            for (int index = 1; index < step.length; index++) if (step[index] > step[best]) best = index;
            if (!Float.isFinite(step[best])) throw new IllegalArgumentException("Non-finite CTC probability");
            if (best != 0 && best != previous) {
                text.append(dictionary[best]); sum += step[best]; emitted++;
            }
            previous = best;
        }
        return new Decoded(text.toString(), emitted == 0 ? 0 : sum / emitted);
    }

    static float[] normalizeBgr(byte[] bgr, int height, int width, int paddedWidth) {
        if (height <= 0 || width <= 0 || paddedWidth < width || bgr.length != height * width * 3)
            throw new IllegalArgumentException("Invalid BGR tensor geometry");
        float[] result = new float[3 * height * paddedWidth];
        for (int y = 0; y < height; y++) for (int x = 0; x < width; x++) {
            int pixel = (y * width + x) * 3;
            for (int channel = 0; channel < 3; channel++)
                result[(channel * height + y) * paddedWidth + x] = ((bgr[pixel + channel] & 255) / 255f - .5f) / .5f;
        }
        return result;
    }

    static int recWidth(int width, int height) {
        if (width <= 0 || height <= 0) throw new IllegalArgumentException("Empty OCR crop");
        return (int)(48 * Math.max(320.0 / 48, width / (double)height));
    }

    /** Returns H,W, including Python round's ties-to-even semantics. */
    static int[] resize32(int height, int width, int limit, boolean minimum) {
        if (height <= 0 || width <= 0) throw new IllegalArgumentException("Empty image");
        double ratio = minimum ? Math.max(1., limit / (double)Math.min(height,width))
                               : Math.min(1., limit / (double)Math.max(height,width));
        int h = (int)(Math.rint(((int)(height * ratio)) / 32.0) * 32);
        int w = (int)(Math.rint(((int)(width * ratio)) / 32.0) * 32);
        if (h <= 0 || w <= 0) throw new IllegalArgumentException("图片过于狭长，请分段拍摄或缩小选区");
        return new int[] {h,w};
    }

    /** DB first reduces each contour to a rectangle. Its round offset has the
     * same enclosing rectangle as extending both local axes by area*ratio/perimeter.
     * This avoids introducing a Java polygon-offset dependency. Float geometry
     * differs slightly from PyClipper's intermediate integer quantization.
     */
    static double[][] unclipRectangle(double[][] box, double ratio) {
        double ux = box[1][0]-box[0][0], uy=box[1][1]-box[0][1];
        double vx = box[3][0]-box[0][0], vy=box[3][1]-box[0][1];
        double width=Math.hypot(ux,uy), height=Math.hypot(vx,vy);
        if (width == 0 || height == 0) return box;
        double distance=width*height*ratio/(2*(width+height));
        return unclipRectangleByDistance(box,distance);
    }

    /** Expand a rectangle using a distance obtained from the actual text
     * contour's area/perimeter, avoiding curved-line whitespace inflation. */
    static double[][] unclipRectangleByDistance(double[][] box,double distance) {
        if(!Double.isFinite(distance)||distance<0)throw new IllegalArgumentException("Invalid DB expansion distance");
        double ux=box[1][0]-box[0][0],uy=box[1][1]-box[0][1];
        double vx=box[3][0]-box[0][0],vy=box[3][1]-box[0][1];
        double width=Math.hypot(ux,uy),height=Math.hypot(vx,vy);
        if(width==0||height==0)return box;
        ux=ux/width*distance;uy=uy/width*distance;vx=vx/height*distance;vy=vy/height*distance;
        return new double[][] {{box[0][0]-ux-vx,box[0][1]-uy-vy},
                {box[1][0]+ux-vx,box[1][1]+uy-vy},
                {box[2][0]+ux+vx,box[2][1]+uy+vy},
                {box[3][0]-ux+vx,box[3][1]-uy+vy}};
    }
}
